import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import discord_mcp_bot.server as srv

GUILDS = [{"id": "1", "name": "FujiNet"}, {"id": "2", "name": "Other Place"}]
CHANNELS = {
    "1": [
        {"id": "5", "type": 4, "name": "Events", "position": 0},
        {"id": "20", "type": 0, "name": "📅retro-events", "position": 0, "parent_id": "5"},
        {"id": "10", "type": 0, "name": "general", "position": 1},
        {"id": "30", "type": 0, "name": "coco-help", "position": 2},
        {"id": "31", "type": 0, "name": "coco-dev", "position": 3},
    ],
    "2": [
        {"id": "40", "type": 0, "name": "general", "position": 0},
        {"id": "41", "type": 0, "name": "fujinet-news", "position": 1},
    ],
}
THREADS = {"1": [{"id": "99", "name": "atari questions", "parent_id": "10", "type": 11}], "2": []}
CHANNEL_GUILD = {c["id"]: g for g, cs in CHANNELS.items() for c in cs} | {"99": "1"}
MEMBERS = [
    {"nick": None, "user": {"id": "7", "username": "tschak909", "global_name": "Thom Cherryhomes"}},
    {"nick": "Thomas", "user": {"id": "8", "username": "thomasx", "global_name": None}},
]


def message(channel_id: str, message_id: str = "200", **extra) -> dict:
    return {
        "id": message_id,
        "channel_id": channel_id,
        "timestamp": "2026-09-30T12:34:56+00:00",
        "type": 0,
        "author": {"username": "bot", "global_name": "Bot"},
        "content": "hi",
        "attachments": [],
        "embeds": [],
        **extra,
    }


class FakeDiscord:
    """Just enough of the Discord REST API for the tools, recording every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.search_calls = 0
        self.forbidden: set[str] = set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path.removeprefix("/api/v10")
        parts = path.strip("/").split("/")
        if path == "/users/@me/guilds":
            return httpx.Response(200, json=GUILDS)
        if parts[0] == "guilds":
            gid, kind = parts[1], parts[2]
            if kind == "channels":
                return httpx.Response(200, json=CHANNELS[gid])
            if kind == "threads":
                return httpx.Response(200, json={"threads": THREADS[gid]})
            if kind == "members":
                q = req.url.params["query"].lower()
                hits = [
                    m for m in MEMBERS
                    if any(q in (n or "").lower() for n in (m["nick"], m["user"]["username"], m["user"]["global_name"]))
                ]
                return httpx.Response(200, json=hits)
            if kind == "messages":
                self.search_calls += 1
                if self.search_calls == 1:
                    return httpx.Response(202, json={"retry_after": 0.01})
                return httpx.Response(200, json={"total_results": 1, "messages": [[message("30")]]})
        if parts[0] == "channels":
            cid = parts[1]
            if cid in self.forbidden:
                return httpx.Response(403, json={"message": "Missing Access", "code": 50001})
            if len(parts) == 2:
                name = next((c["name"] for cs in CHANNELS.values() for c in cs if c["id"] == cid), "x")
                return httpx.Response(200, json={"id": cid, "name": name, "type": 0, "guild_id": CHANNEL_GUILD[cid]})
            if len(parts) == 3:
                if req.method == "GET":
                    return httpx.Response(200, json=[message(cid, "201"), message(cid, "200")])
                return httpx.Response(200, json=message(cid))
            if req.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json=message(cid, parts[3]))
        return httpx.Response(404, json={"message": f"unhandled {path}"})

    def last(self, method: str) -> httpx.Request:
        return next(r for r in reversed(self.requests) if r.method == method)


@pytest.fixture
def discord(tmp_path, monkeypatch):
    fake = FakeDiscord()
    monkeypatch.setattr(srv, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(srv, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(srv, "_cache", {})
    monkeypatch.setattr(
        srv, "_client", httpx.AsyncClient(base_url=srv.API, transport=httpx.MockTransport(fake))
    )
    return fake


def run(coro):
    return asyncio.run(coro)


def header(out: str) -> str:
    return out.splitlines()[0]


def test_tools_and_annotations():
    tools = {t.name: t for t in run(srv.mcp.list_tools())}
    assert set(tools) == {
        "list_servers", "list_channels", "select_channel", "read_messages",
        "search_messages", "send_message", "edit_message", "delete_message",
    }
    assert tools["delete_message"].annotations.destructive_hint
    assert tools["read_messages"].annotations.read_only_hint
    assert not tools["send_message"].annotations.read_only_hint


def test_asks_for_server_when_none_chosen(discord):
    with pytest.raises(srv.NeedsChoice, match="No server chosen") as e:
        run(srv.read_messages())
    assert "FujiNet" in str(e.value) and "Other Place" in str(e.value)


def test_loose_channel_name_searches_all_servers_then_is_remembered(discord):
    assert header(run(srv.read_messages(channel="retro events channel"))) == "#📅retro-events in FujiNet"
    assert header(run(srv.read_messages())) == "#📅retro-events in FujiNet"


def test_same_name_in_two_servers_asks(discord):
    with pytest.raises(srv.NeedsChoice, match="more than one channel") as e:
        run(srv.read_messages(channel="general"))
    assert "(FujiNet)" in str(e.value) and "(Other Place)" in str(e.value)


def test_ambiguous_partial_name_asks(discord):
    run(srv.select_channel("general", "fujinet"))
    with pytest.raises(srv.NeedsChoice) as e:
        run(srv.read_messages(channel="coco"))
    assert "#coco-help" in str(e.value) and "#coco-dev" in str(e.value)


def test_typo_and_thread_names(discord):
    run(srv.select_channel("coco help", "fujinet"))
    assert header(run(srv.read_messages(channel="generl"))) == "#general in FujiNet"
    assert header(run(srv.read_messages(channel="the atari questions thread"))) == (
        "thread 'atari questions' in #general in FujiNet"
    )


def test_channel_missing_from_current_server_found_in_another(discord):
    run(srv.select_channel("general", "fujinet"))
    assert header(run(srv.read_messages(channel="fujinet news"))) == "#fujinet-news in Other Place"
    assert header(run(srv.read_messages(server="fujinet"))) == "#general in FujiNet"


def test_explicit_server_does_not_fall_back(discord):
    with pytest.raises(srv.NotFound, match="No channel matching 'coco help' in Other Place"):
        run(srv.read_messages(server="other", channel="coco help"))


def test_messages_come_back_oldest_first(discord):
    out = run(srv.read_messages(channel="general", server="fujinet"))
    assert out.index("[200]") < out.index("[201]")


def test_send_with_attachment_and_reply_link(discord, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    out = run(srv.send_message("yo", attachments=[str(f)], reply_to="https://discord.com/channels/1/30/555"))
    assert header(out).startswith("Sent to #coco-help in FujiNet.")
    body = discord.last("POST").content.decode()
    assert 'name="files[0]"' in body
    payload = json.loads(body.split('name="payload_json"')[1].split("\r\n\r\n")[1].split("\r\n")[0])
    assert payload["message_reference"] == {"message_id": "555", "fail_if_not_exists": False}
    assert payload["attachments"] == [{"id": 0, "filename": "a.txt"}]
    assert payload["allowed_mentions"]["parse"] == ["users"]


def test_send_needs_something_to_send(discord):
    with pytest.raises(srv.DiscordError, match="Need content"):
        run(srv.send_message(channel="general", server="fujinet"))
    with pytest.raises(srv.DiscordError, match="Not a file"):
        run(srv.send_message("x", channel="general", server="fujinet", attachments=["/no/such/file"]))


def test_edit_uses_remembered_channel(discord):
    run(srv.select_channel("coco dev", "fujinet"))
    assert header(run(srv.edit_message("200", "fixed"))).startswith("Edited in #coco-dev in FujiNet.")
    assert discord.last("PATCH").url.path.endswith("/channels/31/messages/200")


def test_delete_by_link_fetches_then_deletes(discord):
    out = run(srv.delete_message("https://discord.com/channels/2/41/777"))
    assert header(out) == "Deleted from #fujinet-news in Other Place:"
    assert [r.method for r in discord.requests[-2:]] == ["GET", "DELETE"]


def test_search_retries_while_indexing_and_resolves_names(discord):
    run(srv.select_channel("general", "fujinet"))
    out = run(srv.search_messages("coco", authors=["tschak909"], channels=["coco help"], has=["file"]))
    assert header(out) == "FujiNet: 1 result, showing 1-1"
    assert discord.search_calls == 2
    params = discord.last("GET").url.params
    assert params.get_list("channel_id") == ["30"]
    assert params.get_list("author_id") == ["7"]
    assert params.get_list("has") == ["file"]


def test_ambiguous_author_asks(discord):
    with pytest.raises(srv.NeedsChoice) as e:
        run(srv.search_messages("x", server="fujinet", authors=["thom"]))
    assert "@tschak909" in str(e.value) and "@thomasx" in str(e.value)


def test_reactions_are_shown():
    m = message("1", reactions=[
        {"emoji": {"id": None, "name": "👍"}, "count": 3},
        {"emoji": {"id": "99", "name": "fujinet"}, "count": 1},
    ])
    assert "  reactions: 👍 3, :fujinet: 1" in srv._fmt_message(m)
    assert "reactions" not in srv._fmt_message(message("1"))


def test_blank_messages_hint_at_intent():
    out = srv._fmt_messages([message("1", content="")])
    assert "Message Content Intent" in out


def test_discord_errors_reach_the_user(discord):
    discord.forbidden.add("10")
    with pytest.raises(ToolError, match="Discord 403: Missing Access"):
        run(srv.mcp.call_tool("read_messages", {"channel": "general", "server": "fujinet"}))


def test_send_links_mentions(discord):
    out = run(srv.send_message("@tschak909 @Thomas `@thomasx` @thom @nobody @everyone hi", channel="coco-help"))
    sent = json.loads(discord.last("POST").content)["content"]
    assert sent == "<@7> <@8> `@thomasx` @thom @nobody @everyone hi"
    assert "Tagged: @tschak909, @Thomas." in header(out)


def test_edit_links_mentions(discord):
    out = run(srv.edit_message("https://discord.com/channels/1/30/555", "ping @tschak909"))
    assert json.loads(discord.last("PATCH").content)["content"] == "ping <@7>"
    assert "Tagged: @tschak909." in header(out)


def test_send_without_mentions_skips_member_lookup(discord):
    run(srv.send_message("no pings here", channel="coco-help"))
    assert not any("/members/" in r.url.path for r in discord.requests)
