import asyncio
import difflib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Annotated, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

API = "https://discord.com/api/v10"
CONFIG_DIR = Path.home() / ".config" / "discord-mcp"
TOKEN_FILE = CONFIG_DIR / "token"
STATE_FILE = CONFIG_DIR / "state.json"
MAX_CONTENT = 2000
MAX_FILES = 10
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_RETRY_WAIT = 10.0
CACHE_TTL = 60.0
MAX_CHOICES = 60

CHANNEL_TYPES = {0: "text", 5: "announcement", 10: "thread", 11: "thread", 12: "private thread"}
CATEGORY = 4
FILLER = {"the", "channel", "chan", "server", "guild", "thread", "room", "in", "on"}
logging.getLogger("httpx").setLevel(logging.WARNING)

LINK = re.compile(r"discord(?:app)?\.com/channels/(\d+|@me)/(\d+)/(\d+)")

READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

Server = Annotated[
    str | None,
    Field(description="Server name or ID, as the user said it. Omit to use the last server used."),
]
Channel = Annotated[
    str | None,
    Field(
        description="Channel or thread name or ID, as the user said it (e.g. 'retro events'). "
        "Omit to use the last channel used in that server."
    ),
]
MessageRef = Annotated[str, Field(description="Message ID or message link")]

mcp = MCPServer(
    "discord",
    instructions=(
        "Reads, searches, and posts Discord messages as a bot. Pass server and channel names "
        "the way the user said them; the server matches them loosely and remembers the last "
        "server and channel used, so omit them when the user does. If a tool reports that a "
        "choice is needed, show the user the options and ask; never pick one yourself. Each "
        "result names the server and channel it used. Timestamps are UTC."
    ),
)


# The SDK hides the text of any exception that isn't a ToolError.
class DiscordError(ToolError):
    pass


class NeedsChoice(DiscordError):
    pass


class NotFound(NeedsChoice):
    pass


_client: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, object]] = {}


def _token() -> str:
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
    if not token:
        raise DiscordError(f"No bot token: set DISCORD_BOT_TOKEN or write it to {TOKEN_FILE}")
    return token


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=API,
            headers={
                "Authorization": f"Bot {_token()}",
                "User-Agent": "DiscordBot (discord-mcp-bot, 0.1.0)",
            },
            timeout=30,
        )
    return _client


async def _request(method: str, path: str, **kwargs) -> dict | list:
    # 429 is a rate limit; 202 is search waiting on the index. Both carry retry_after in seconds.
    for _ in range(5):
        try:
            r = await _http().request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise DiscordError(f"Could not reach Discord: {e}") from e
        if r.status_code in (202, 429):
            wait = float(r.json().get("retry_after", 1))
            if wait > MAX_RETRY_WAIT:
                raise DiscordError(f"Discord asked to retry in {wait:.0f}s; try again later")
            await asyncio.sleep(wait)
            continue
        if r.is_error:
            try:
                detail = r.json().get("message", r.text)
            except ValueError:
                detail = r.text
            raise DiscordError(f"Discord {r.status_code}: {detail}")
        return r.json() if r.content else {}
    raise DiscordError("Discord kept rate-limiting the request; try again later")


async def _get(path: str, fresh: bool = False) -> dict | list:
    hit = _cache.get(path)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    val = await _request("GET", path)
    _cache[path] = (time.monotonic(), val)
    return val


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _remember(guild: dict, channel: dict | None = None) -> None:
    state = _load_state()
    state["server"] = guild["id"]
    if channel:
        state.setdefault("channels", {})[guild["id"]] = channel["id"]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_FILE)


async def _servers(fresh: bool = False) -> list[dict]:
    return await _get("/users/@me/guilds", fresh)


async def _channels(guild: dict, fresh: bool = False) -> list[dict]:
    """Text channels and active threads, each tagged with its server and a display label."""
    chans, active = await asyncio.gather(
        _get(f"/guilds/{guild['id']}/channels", fresh),
        _get(f"/guilds/{guild['id']}/threads/active", fresh),
    )
    names = {c["id"]: c["name"] for c in chans}
    out = [{**c, "guild": guild, "label": f"#{c['name']}"} for c in chans if c["type"] in CHANNEL_TYPES]
    for t in active.get("threads", []):
        parent = names.get(t.get("parent_id"), "?")
        out.append({**t, "guild": guild, "label": f"thread '{t['name']}' in #{parent}"})
    return out


def _key(s: str) -> str:
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def _choices(msg: str, items: list[dict]) -> str:
    multi = len({it["guild"]["id"] for it in items if "guild" in it}) > 1
    lines = [
        f"  {it.get('label', it['name'])}" + (f" ({it['guild']['name']})" if multi else "")
        for it in items[:MAX_CHOICES]
    ]
    if len(items) > MAX_CHOICES:
        lines.append(f"  ...and {len(items) - MAX_CHOICES} more")
    return f"{msg}:\n" + "\n".join(lines) + "\nAsk the user which one they mean."


def _match(query: str, items: list[dict], kind: str, where: str = "") -> dict:
    """Match loosely: 'retro events channel' finds #retro-events or #📅retro_events."""
    q = query.strip().lstrip("#@").strip()
    for it in items:
        if it["id"] == q:
            return it
    all_words = re.findall(r"[^\W_]+", q.casefold())
    words = [w for w in all_words if w not in FILLER] or all_words
    core = "".join(words)
    if not core:
        raise NotFound(_choices(f"No {kind} matching '{query}'{where}", items))
    keys = [_key(it["name"]) for it in items]
    for test in (
        lambda k: k in (core, _key(q)),
        lambda k: core in k,
        lambda k: all(w in k for w in words),
    ):
        hits = [it for it, k in zip(items, keys) if test(k)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise NeedsChoice(_choices(f"'{query}' could mean more than one {kind}{where}", hits))
    close = set(difflib.get_close_matches(core, keys, n=5, cutoff=0.75))
    hits = [it for it, k in zip(items, keys) if k in close]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise NeedsChoice(_choices(f"'{query}' could mean more than one {kind}{where}", hits))
    raise NotFound(_choices(f"No {kind} matching '{query}'{where}", items))


async def _find(query: str, load, kind: str, where: str = "") -> dict:
    """Match against cached lists, then once more against fresh ones in case something is new."""
    try:
        return _match(query, await load(False), kind, where)
    except NeedsChoice:
        return _match(query, await load(True), kind, where)


async def _resolve_server(server: str | None) -> dict:
    guilds = await _servers()
    if not guilds:
        raise DiscordError("The bot is not in any servers yet")
    if server:
        return await _find(server, _servers, "server")
    last = _load_state().get("server")
    for g in guilds:
        if g["id"] == last:
            return g
    if len(guilds) == 1:
        return guilds[0]
    raise NeedsChoice(_choices("No server chosen yet", guilds))


async def _all_channels(fresh: bool) -> list[dict]:
    lists = await asyncio.gather(*(_channels(g, fresh) for g in await _servers(fresh)))
    return [c for chans in lists for c in chans]


async def _resolve(server: str | None, channel: str | None) -> tuple[dict, dict]:
    if channel and channel.strip().isdigit():
        raw = await _request("GET", f"/channels/{channel.strip()}")
        g = next((g for g in await _servers() if g["id"] == raw.get("guild_id")), None)
        if not g:
            raise DiscordError(f"Channel {channel} is not in a server the bot belongs to")
        listed = next((c for c in await _channels(g) if c["id"] == raw["id"]), None)
        c = listed or {**raw, "guild": g, "label": f"#{raw['name']}"}
    elif channel:
        g = None if not server and not _load_state().get("server") else await _resolve_server(server)
        c = None
        if g:
            try:
                c = await _find(channel, lambda fresh: _channels(g, fresh), "channel", f" in {g['name']}")
            except NotFound:
                if server or len(await _servers()) == 1:
                    raise
        if not c:
            c = await _find(channel, _all_channels, "channel", " in any server")
            g = c["guild"]
    else:
        g = await _resolve_server(server)
        chans = await _channels(g)
        last = _load_state().get("channels", {}).get(g["id"])
        c = next((c for c in chans if c["id"] == last), None)
        if not c:
            raise NeedsChoice(_choices(f"No channel chosen yet in {g['name']}", chans))
    _remember(g, c)
    return g, c


def _parse_message(ref: str, server: str | None, channel: str | None) -> tuple[str, str | None, str | None]:
    """Split a message link into (message ID, server ID, channel ID); plain IDs pass through."""
    m = LINK.search(ref)
    if m:
        return m.group(3), m.group(1), m.group(2)
    return ref.strip(), server, channel


def _where(g: dict, c: dict) -> str:
    return f"{c['label']} in {g['name']}"


def _link(g: dict, msg: dict) -> str:
    return f"https://discord.com/channels/{g['id']}/{msg['channel_id']}/{msg['id']}"


def _fmt_message(m: dict, channel_names: dict[str, str] | None = None) -> str:
    a = m["author"]
    name = a.get("global_name") or a["username"]
    head = f"[{m['id']}] {m['timestamp'][:16].replace('T', ' ')} {name} (@{a['username']})"
    if channel_names is not None:
        head += f" in #{channel_names.get(m['channel_id'], m['channel_id'])}"
    ref = m.get("message_reference") or {}
    if m.get("type") == 19 and ref.get("message_id"):
        head += f" replying to {ref['message_id']}"
    if m.get("edited_timestamp"):
        head += " (edited)"
    lines = [head]
    lines += ["  " + line for line in (m.get("content") or "").splitlines()]
    for att in m.get("attachments", []):
        lines.append(f"  attachment: {att['filename']} ({att['size']} bytes) {att['url']}")
    for e in m.get("embeds", []):
        if e.get("title") or e.get("url"):
            lines.append(f"  embed: {e.get('title', '')} {e.get('url', '')}".rstrip())
    if m.get("thread"):
        lines.append(f"  thread: {m['thread']['name']} (id {m['thread']['id']})")
    if m.get("reactions"):
        # Custom server emoji have an id; show them by :name:.
        emoji = [
            f"{':' + r['emoji']['name'] + ':' if r['emoji'].get('id') else r['emoji']['name']} {r['count']}"
            for r in m["reactions"]
        ]
        lines.append(f"  reactions: {', '.join(emoji)}")
    return "\n".join(lines)


def _fmt_messages(msgs: list[dict], channel_names: dict[str, str] | None = None) -> str:
    if not msgs:
        return "No messages."
    out = "\n\n".join(_fmt_message(m, channel_names) for m in msgs)
    if all(not m.get("content") and not m.get("attachments") and not m.get("embeds") for m in msgs):
        out += (
            "\n\nEvery message came back empty: enable the Message Content Intent "
            "under Bot in the Discord Developer Portal."
        )
    return out


async def _resolve_authors(g: dict, authors: list[str]) -> list[str]:
    ids = []
    for who in authors:
        who = who.strip().lstrip("@")
        if who.isdigit():
            ids.append(who)
            continue
        try:
            members = await _request(
                "GET", f"/guilds/{g['id']}/members/search", params={"query": who, "limit": 10}
            )
        except DiscordError as e:
            if "403" in str(e):
                raise DiscordError(
                    "Discord refused the member lookup: enable Server Members Intent under Bot "
                    "in the Developer Portal, or search by user ID"
                ) from e
            raise
        items = []
        for mem in members:
            u = mem["user"]
            names = [n for n in (mem.get("nick"), u.get("global_name"), u["username"]) if n]
            items.append({"id": u["id"], "name": names[0], "label": f"{names[0]} (@{u['username']})", "all": names})
        exact = [it for it in items if who.casefold() in (n.casefold() for n in it["all"])]
        if len(exact) == 1 or len(items) == 1:
            ids.append((exact or items)[0]["id"])
        elif not items:
            raise NotFound(f"No member named '{who}' in {g['name']}. Ask the user who they mean.")
        else:
            raise NeedsChoice(_choices(f"'{who}' could mean more than one member of {g['name']}", exact or items))
    return ids


@mcp.tool(annotations=READ)
async def list_servers() -> str:
    """List the Discord servers the bot is a member of."""
    guilds = await _servers(fresh=True)
    if not guilds:
        return "The bot is not in any servers yet."
    last = _load_state().get("server")
    return "\n".join(f"{g['id']}  {g['name']}" + (" (current)" if g["id"] == last else "") for g in guilds)


@mcp.tool(annotations=READ)
async def list_channels(server: Server = None) -> str:
    """List a server's text channels by category, with active threads under their parent."""
    g = await _resolve_server(server)
    _remember(g)
    chans, active = await asyncio.gather(
        _get(f"/guilds/{g['id']}/channels", fresh=True),
        _get(f"/guilds/{g['id']}/threads/active", fresh=True),
    )
    current = _load_state().get("channels", {}).get(g["id"])
    categories = {c["id"]: c for c in chans if c["type"] == CATEGORY}
    threads: dict[str, list[dict]] = {}
    for t in active.get("threads", []):
        threads.setdefault(t.get("parent_id"), []).append(t)
    groups: dict[str | None, list[dict]] = {}
    for c in chans:
        if c["type"] in CHANNEL_TYPES:
            groups.setdefault(c.get("parent_id"), []).append(c)

    def cat_pos(cat_id: str | None) -> int:
        return categories[cat_id]["position"] if cat_id in categories else -1

    def mark(cid: str) -> str:
        return " (current)" if cid == current else ""

    lines = [g["name"]]
    for cat_id in sorted(groups, key=cat_pos):
        lines.append(f"# {categories[cat_id]['name']}" if cat_id in categories else "# (no category)")
        for c in sorted(groups[cat_id], key=lambda c: c["position"]):
            kind = "" if c["type"] == 0 else f" [{CHANNEL_TYPES[c['type']]}]"
            topic = f" - {c['topic']}" if c.get("topic") else ""
            lines.append(f"  {c['id']}  #{c['name']}{kind}{mark(c['id'])}{topic}")
            for t in threads.get(c["id"], []):
                lines.append(f"      {t['id']}  thread: {t['name']}{mark(t['id'])}")
    return "\n".join(lines)


@mcp.tool(annotations=READ)
async def select_channel(channel: Channel = None, server: Server = None) -> str:
    """Make a server and channel the default for later calls."""
    g, c = await _resolve(server, channel)
    return f"Now using {_where(g, c)}."


@mcp.tool(annotations=READ)
async def read_messages(
    channel: Channel = None,
    server: Server = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
    before: Annotated[str | None, Field(description="Only messages older than this message ID")] = None,
    after: Annotated[str | None, Field(description="Only messages newer than this message ID")] = None,
    around: Annotated[str | None, Field(description="Messages surrounding this message ID")] = None,
) -> str:
    """Read messages from a channel or thread, oldest first. With no cursor, returns the latest."""
    g, c = await _resolve(server, channel)
    params = {"limit": limit}
    for key, val in (("before", before), ("after", after), ("around", around)):
        if val:
            params[key] = val
    msgs = await _request("GET", f"/channels/{c['id']}/messages", params=params)
    return f"{_where(g, c)}\n\n{_fmt_messages(sorted(msgs, key=lambda m: int(m['id'])))}"


@mcp.tool(annotations=READ)
async def search_messages(
    query: Annotated[str | None, Field(description="Text to search for")] = None,
    server: Server = None,
    channels: Annotated[list[str] | None, Field(description="Limit to these channel names or IDs")] = None,
    authors: Annotated[list[str] | None, Field(description="Limit to these people, by name or user ID")] = None,
    has: Annotated[
        list[Literal["image", "sound", "video", "file", "sticker", "embed", "link", "poll"]] | None,
        Field(description="Only messages containing these"),
    ] = None,
    attachment_extensions: Annotated[list[str] | None, Field(description="e.g. ['pdf', 'zip']")] = None,
    pinned: bool | None = None,
    sort_by: Literal["timestamp", "relevance"] = "timestamp",
    sort_order: Literal["desc", "asc"] = "desc",
    limit: Annotated[int, Field(ge=1, le=25)] = 25,
    offset: Annotated[int, Field(ge=0, le=9975, description="For paging through results")] = 0,
) -> str:
    """Search a server's message history. Searches the whole server unless channels are given."""
    g = await _resolve_server(server)
    _remember(g)
    chans = await _channels(g)
    channel_ids = [
        (await _find(ch, lambda fresh: _channels(g, fresh), "channel", f" in {g['name']}"))["id"]
        for ch in channels or []
    ]
    params: list[tuple[str, str | int]] = [
        ("sort_by", sort_by),
        ("sort_order", sort_order),
        ("limit", limit),
        ("offset", offset),
    ]
    if query:
        params.append(("content", query))
    if pinned is not None:
        params.append(("pinned", str(pinned).lower()))
    for key, vals in (
        ("channel_id", channel_ids),
        ("author_id", await _resolve_authors(g, authors or [])),
        ("has", has),
        ("attachment_extension", attachment_extensions),
    ):
        params += [(key, v) for v in vals or []]

    res = await _request("GET", f"/guilds/{g['id']}/messages/search", params=params)
    msgs = [group[0] for group in res.get("messages", []) if group]
    if not msgs:
        return f"{g['name']}: no matches."
    total = res.get("total_results", len(msgs))
    header = f"{g['name']}: {total} result{'' if total == 1 else 's'}, showing {offset + 1}-{offset + len(msgs)}"
    if res.get("doing_deep_historical_index"):
        header += " (Discord is still indexing older history; results may be incomplete)"
    names = {c["id"]: c["name"] for c in chans}
    return f"{header}\n\n{_fmt_messages(msgs, names)}"


@mcp.tool(annotations=WRITE)
async def send_message(
    content: Annotated[str, Field(max_length=MAX_CONTENT)] = "",
    channel: Channel = None,
    server: Server = None,
    attachments: Annotated[list[str] | None, Field(description="Local file paths to attach (max 10)")] = None,
    reply_to: Annotated[str | None, Field(description="Message ID or link to reply to")] = None,
) -> str:
    """Post a message, optionally with file attachments or as a reply. @everyone and role pings are suppressed."""
    if reply_to:
        reply_to, server, channel = _parse_message(reply_to, server, channel)
    paths = [Path(p).expanduser() for p in attachments or []]
    if not content and not paths:
        raise DiscordError("Need content, attachments, or both")
    if len(paths) > MAX_FILES:
        raise DiscordError(f"At most {MAX_FILES} attachments per message")
    for p in paths:
        if not p.is_file():
            raise DiscordError(f"Not a file: {p}")
    if sum(p.stat().st_size for p in paths) > MAX_UPLOAD_BYTES:
        raise DiscordError("Attachments exceed 25 MiB total")

    g, c = await _resolve(server, channel)
    payload: dict = {"content": content, "allowed_mentions": {"parse": ["users"], "replied_user": True}}
    if reply_to:
        payload["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}

    if paths:
        payload["attachments"] = [{"id": i, "filename": p.name} for i, p in enumerate(paths)]
        files = [(f"files[{i}]", (p.name, p.read_bytes())) for i, p in enumerate(paths)]
        msg = await _request(
            "POST",
            f"/channels/{c['id']}/messages",
            data={"payload_json": json.dumps(payload)},
            files=files,
        )
    else:
        msg = await _request("POST", f"/channels/{c['id']}/messages", json=payload)
    return f"Sent to {_where(g, c)}. {_link(g, msg)}\n\n{_fmt_message(msg)}"


@mcp.tool(annotations=WRITE)
async def edit_message(
    message: MessageRef,
    content: Annotated[str, Field(max_length=MAX_CONTENT)],
    channel: Channel = None,
    server: Server = None,
) -> str:
    """Replace the text of a message the bot posted. Attachments are kept."""
    message_id, server, channel = _parse_message(message, server, channel)
    g, c = await _resolve(server, channel)
    msg = await _request(
        "PATCH",
        f"/channels/{c['id']}/messages/{message_id}",
        json={"content": content, "allowed_mentions": {"parse": ["users"]}},
    )
    return f"Edited in {_where(g, c)}. {_link(g, msg)}\n\n{_fmt_message(msg)}"


@mcp.tool(annotations=DELETE)
async def delete_message(message: MessageRef, channel: Channel = None, server: Server = None) -> str:
    """Delete a message. The bot can delete its own messages, or anyone's if it has Manage Messages."""
    message_id, server, channel = _parse_message(message, server, channel)
    g, c = await _resolve(server, channel)
    msg = await _request("GET", f"/channels/{c['id']}/messages/{message_id}")
    await _request("DELETE", f"/channels/{c['id']}/messages/{message_id}")
    return f"Deleted from {_where(g, c)}:\n\n{_fmt_message(msg)}"


def main() -> None:
    mcp.run()
