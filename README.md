# discord-mcp-bot

Lets an AI assistant such as Claude or Codex read, search, and post in Discord, including attaching files, through a Discord bot you create. Once it's set up, you just ask in plain words: "read the retro events channel", "search for BASIC", "post my last screenshot to general".

## What you're setting up

- **An MCP server** is a small add-on program for an AI assistant. The assistant starts it in the background, and the add-on gives it new abilities. This one gives it Discord abilities. You never run it yourself.
- **A Discord bot** is a separate Discord account that belongs to you. The server acts through the bot, not through your personal account. Logging in as yourself this way breaks Discord's Terms of Service and can get your account banned.
- The bot can see only the servers it has been **invited** to. Inviting it takes someone with the **Manage Server** permission on that server. You have that on any server you create, and you'll need a server's admins to invite it anywhere else.

Setup takes about 15 minutes. Do the parts in order. The terminal commands are for Linux and macOS.

### Shortcut: use the existing MCP Bot

If you only want this for the FujiNet Discord, you can skip creating a bot and use **MCP Bot**, which is already in that server. Message **@richstephens** on Discord to ask for its token, which he'll send you privately. Then do only Parts 1, 4, 6, and 7, saving the token he sent in Part 4.

Before you ask, know that:
- Everything you post appears as **MCP Bot**, not as you.
- The token gives full control of the bot. Never share it or paste it anywhere public.
- It works only in servers MCP Bot has joined.
- If the token is ever reset, you'll need to ask for the new one.

## Part 1: Install the prerequisites

1. **An AI assistant that runs on your computer:** Claude Code, Claude Desktop, or Codex. Part 7 covers each one. The Claude and ChatGPT websites and phone apps can't use this, because they can only reach add-ons hosted on the internet.
2. **uv**, which runs the server. Check whether you have it:
   ```sh
   uv --version
   ```
   If that says "command not found", install it, then close and reopen your terminal:
   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. **This project.** Download it into your home directory; the steps below assume it's at `~/discord-mcp-bot`:
   ```sh
   git clone https://github.com/RichStephens/discord-mcp-bot.git ~/discord-mcp-bot
   ```

## Part 2: Make a test server (recommended)

Try things out on a private server of your own first, where nobody else sees your test posts.

1. In Discord, click the **+** at the bottom of the server icons on the left.
2. Choose **Create My Own**, then **For me and my friends**, name it, and click **Create**.
3. Add a few text channels: click **+** next to **TEXT CHANNELS**, choose **Text**, enter a name, and click **Create Channel**.
4. Post a few messages so there's something to read and search.

## Part 3: Create the bot

1. Go to <https://discord.com/developers/applications> and sign in with your normal Discord account.
2. Click **New Application**, give it a name (this becomes the bot's name), accept the terms, and click **Create**.
3. On the **General Information** page, click **Copy** under **Application ID** and paste it somewhere. You'll need it in Part 5. It isn't secret.
4. Click **Bot** in the left menu.
5. Click **Reset Token**, confirm, and enter your password or 2FA code if asked. Click **Copy** and **keep this tab open** until Part 4 is done, because Discord shows the token only once. If you lose it, click **Reset Token** again and use the new one.

   > The token is the bot's password. Don't paste it into chats, emails, or files you share. If it ever leaks, click **Reset Token**; the old one stops working immediately.

6. Scroll down to **Privileged Gateway Intents** and turn on both of these:
   - **Message Content Intent.** Without it, every message reads as blank.
   - **Server Members Intent.** Without it, you can't search for messages by a person's name.
7. Click **Save Changes** in the bar at the bottom.

**Optional: make the bot private,** so nobody else can add it to their servers. Private means only you can add it, so admins of other servers can't. Leave it public until every server you need has added it; turning it private afterward doesn't remove it from those servers. You must change two settings, in this order, or the save fails:

1. Click **Installation** in the left menu. Under **Install Link**, choose **None**, then click **Save Changes**.
2. Go back to **Bot**, turn off **Public Bot**, and click **Save Changes**.

> **Watch out:** if a save on the Bot page fails, Discord resets every switch on that page, including the two intents from step 6. After any error, turn them back on and save again.

## Part 4: Save the token on your computer

1. Open a terminal and run this. It's all one line; copy the whole thing:
   ```sh
   mkdir -p -m 700 ~/.config/discord-mcp && read -rsp "Paste token: " t && printf '%s\n' "$t" > ~/.config/discord-mcp/token && chmod 600 ~/.config/discord-mcp/token && unset t && echo " saved"
   ```
2. At `Paste token:`, paste the token. In most Linux terminals, paste is **Ctrl-Shift-V**, not Ctrl-V. **Nothing appears as you paste.** That's deliberate, so the token doesn't show on screen.
3. Press **Enter**. You should see `saved`.

**Do not use `sudo`.** It makes the file owned by root, and the server, which runs as you, can't read it. If you already did, run `sudo rm ~/.config/discord-mcp/token` and repeat this part.

To check it worked, run:
```sh
ls -l ~/.config/discord-mcp/token
```
The line should start with `-rw-------`, show your username (not `root`), and show a size around 70 bytes, not 0 or 1.

## Part 5: Invite the bot to your server

1. Take this link and replace `APP_ID` with your Application ID from Part 3:
   ```
   https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot&permissions=274878008320
   ```
2. Open it in your browser, choose your server from the dropdown, and click **Continue**, then **Authorize**. Leave the permission boxes ticked. The bot asks only to view channels, read message history, send messages (including in threads), and attach files.
3. The bot now appears in the server's member list. **It shows as offline, and that's normal.** It contacts Discord only when you ask it to do something.

For a server you don't run, send this same link to one of its admins and ask them to authorize it. An ordinary `discord.gg/...` invite won't work, because those are for people, not bots.

## Part 6: Check that the bot works

Run this in a terminal before involving your AI assistant:
```sh
cd ~/discord-mcp-bot && uv run python -c "import asyncio, discord_mcp_bot.server as s; print(asyncio.run(s.list_servers()))"
```
The first run takes a moment while it installs what it needs. It should print your server's ID and name. If it prints an error instead, see [Troubleshooting](#troubleshooting).

## Part 7: Connect it to your AI assistant

Follow the section for the assistant you use. In every one of them, **the assistant loads add-ons only when it starts,** so you have to quit and restart it afterward. A window or session that was already open won't see the new add-on.

### Claude Code

1. In a terminal (not inside Claude Code), run:
   ```sh
   claude mcp add --scope user discord -- uv --directory ~/discord-mcp-bot run discord-mcp-bot
   ```
   `--scope user` makes it available in every folder, not just the current one.
2. If Claude Code is running, quit it with `/exit`, then start it again. To pick up the conversation where you left off, start it with `claude --continue`.
3. Type `/mcp`. `discord` should be listed as connected.

### Codex

The Codex command-line tool, the Codex extension for VS Code and other editors, and the ChatGPT desktop app all share this setting, so you only do it once.

1. In a terminal, run:
   ```sh
   codex mcp add discord -- uv --directory ~/discord-mcp-bot run discord-mcp-bot
   ```
2. Quit Codex and start it again. In the editor extension, choose **Restart extension**.
3. Type `/mcp` in Codex, or run `codex mcp list` in a terminal. `discord` should be listed.

If you'd rather edit the settings file directly, add this to `~/.codex/config.toml`, replacing `YOURNAME` with your username:
```toml
[mcp_servers.discord]
command = "uv"
args = ["--directory", "/home/YOURNAME/discord-mcp-bot", "run", "discord-mcp-bot"]
```

### Claude Desktop

Claude Desktop runs on macOS and Windows.

1. In a terminal, run `which uv` and note the full path it prints, e.g. `/Users/YOURNAME/.local/bin/uv`. Desktop apps often can't find `uv` by its short name.
2. In Claude Desktop, open the **Claude** menu in the menu bar (not the settings inside the chat window) and choose **Settings…**. Click **Developer**, then **Edit Config**. This opens a file called `claude_desktop_config.json`.
3. If the file is empty or contains only `{}`, replace its contents with the following. Use your path from step 1, and replace `YOURNAME` with your username:
   ```json
   {
     "mcpServers": {
       "discord": {
         "command": "/Users/YOURNAME/.local/bin/uv",
         "args": ["--directory", "/Users/YOURNAME/discord-mcp-bot", "run", "discord-mcp-bot"]
       }
     }
   }
   ```
   If the file already has an `"mcpServers"` section, add just the `"discord": { ... }` part inside it, with a comma after the entry before it.
4. Save the file. **Fully quit** Claude Desktop from its menu (closing the window isn't enough), then open it again.
5. Click the **+** at the bottom left of the message box, then **Connectors**. `discord` should be in the list.

### Other assistants

Any assistant that supports "local" or "stdio" MCP servers can use this. Give it the name `discord`, the command `uv`, and the arguments `--directory /home/YOURNAME/discord-mcp-bot run discord-mcp-bot`.

## Using it

Just ask in plain words. For example:

| You say | What happens |
|---|---|
| "What Discord servers are you in?" | Lists the servers the bot has been invited to. |
| "Show me the channels" | Lists the channels and active threads, grouped by category. |
| "Read the retro events channel" | Shows recent messages in `#retro-events`, with attachments and reactions. |
| "Any reactions to my post in general?" | Shows the post's reactions and counts, e.g. `👍 3, :fujinet: 1`. |
| "Search for BASIC" | Searches the whole server's history. |
| "Find posts by Thom with attachments in coco help" | Searches by person, channel, and attachment type together. |
| "Post 'see you there' in general" | Sends a message. |
| "Post my last screenshot to general" | Attaches a file from your computer (up to 10 files, 25 MB total). |
| "Reply to that saying thanks" | Replies to a message. |
| "Fix the typo in my last post" / "Delete that" | Edits or deletes a message the bot posted. |

Things to know:

- **Names can be loose.** "The retro events channel" finds `#retro-events`, even with dashes, underscores, emoji, or a small typo in the name.
- **If a name could mean more than one thing,** like "coco" when both `#coco-help` and `#coco-dev` exist, you're asked to pick. It won't guess.
- **It remembers where you were.** If you leave out the server or channel, it uses the one you used last, including the last channel you used in each server. "Post that there" works.
- **Messages can be given as links.** In Discord, right-click a message and choose **Copy Message Link**, then say "delete this: *link*" or "reply to *link*".
- **The bot never pings everyone.** `@everyone`, `@here`, and role mentions are switched off. Mentioning a single person still notifies them.
- **Search results don't include reactions.** Discord's search leaves them out. To see a found message's reactions, ask to read the channel around it.
- **Spaces in file names become underscores** when uploaded. Discord does this to every upload.
- **Everything the bot posts appears under the bot's name,** not yours.
- **The bot can edit only its own messages.** It can't edit or delete other people's messages.

## Troubleshooting

| Problem | Fix |
|---|---|
| The token command just sits there | It's waiting for the paste. Nothing shows on screen. Paste with Ctrl-Shift-V and press Enter. |
| "No bot token" | The token file is missing or empty. Redo Part 4. |
| Token file is owned by `root` | It was saved with `sudo`. Run `sudo rm ~/.config/discord-mcp/token` and redo Part 4 without `sudo`. |
| "Discord 401: Unauthorized" | The token is wrong, or it was reset afterward. Reset it (Part 3, step 5) and redo Part 4. |
| "The bot is not in any servers yet" | Invite it (Part 5). |
| Every message comes back blank | Message Content Intent is off. Turn it on and click **Save Changes** (Part 3, step 6). |
| "Discord refused the member lookup" | Server Members Intent is off (Part 3, step 6). |
| "Discord 403: Missing Access" | The bot isn't allowed in that channel. Ask the server's admins to give it access. |
| Search finds nothing or too little | Discord takes a while to index a new server for search. Try again in a few minutes. |
| An admin sees "This integration is private. Only the application owner can add it." | Turn **Public Bot** on (Bot page), have them try again, then turn it back off if you like. |
| "Private application cannot have a default authorization link" | Set **Installation → Install Link** to **None** first, then turn off **Public Bot**. Recheck the intents afterward. |
| The assistant doesn't list `discord`, or shows it as failed | Quit and restart the assistant completely. If it still fails, run `which uv` and use that full path in place of `uv`. Some apps don't search the same places your terminal does. |
| The assistant asks permission before using Discord | That's normal. Approve it, or use the assistant's settings to always allow the read-only tools. |
| It keeps using the wrong server or channel | Name the right one once and it switches. To forget everything, delete `~/.config/discord-mcp/state.json`. |

## Questions people ask

**Why not just use my own Discord login?**
Letting a program act as your personal account (a "self-bot") breaks Discord's Terms of Service, and Discord bans accounts for it. A bot is the supported way.

**What about the Discord Social SDK on the developer page?**
That's for putting Discord features inside video games: friends lists, game invites, voice chat. It can't read, search, or post in servers.

**Can a friend use my bot?**
Only by giving them your token, the bot's password. Anyone who has it controls the bot completely, and you can't cut off one person without resetting the token for everyone. That's fine for people you trust; otherwise each person should create their own bot (Part 3), which an admin of every server it joins has to approve. Posts always show under the bot's name, never under a person's name.

**Can it see all the servers I'm in?**
No, only the ones the bot has been invited to. Every server it's invited to shows up automatically, with nothing else to set up.

## Files it keeps

| File | What it is |
|---|---|
| `~/.config/discord-mcp/token` | The bot token. A `DISCORD_BOT_TOKEN` environment variable overrides it. |
| `~/.config/discord-mcp/state.json` | The server and channels used last. Safe to delete. |
