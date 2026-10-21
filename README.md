# Echo

An event-driven personal agent with iMessage on a separate macOS login.

```text
Your iPhone ↔ Echo login: Messages bridge ↔ Main login: agent ↔ OpenAI
                                                   ↕
                                            Your MCP connections
                                                   ↕
                                     Optional Codex worker on Echo login
```

The bridge reads and sends iMessages. Your main login owns the OpenAI key, agent
logic, and MCP connections. The two processes communicate over authenticated
loopback HTTP on the same Mac. No public port or cloud server is needed.

## Switch your existing installation

Use a separate checkout inside each user's home. Keep the existing Echo checkout
where it is; don't copy its `.venv` or secrets to another user. This change keeps
the old background launchers working until you run setup.

1. **On your main Mac login:** update this repository (`git pull origin main`)
   and open the **scripts** folder, then double-click **Setup Echo.command**.
   Choose **1 — Main login: set up agent**.
   Enter your OpenAI API key if prompted (hidden input), your phone's sender
   address, and the Echo macOS short username (normally `echo`). The wizard creates
   the Python environment if needed, installs dependencies, creates a pairing file
   readable by that named user, and installs the agent and updater.
2. **Switch to the Echo Mac login without logging out of the main login.** Update
   its existing repository and open **scripts → Setup Echo.command** there. Choose
   **2 — Echo login: set up Messages bridge**. Enter your main Mac login's short
   username. It finds a pairing file created by that user, checks
   the main agent, and replaces the old background bot. No OpenAI key is needed
   in this login. Existing Messages Full Disk Access and Automation permissions
   still apply; choose **4 — Repair Messages access** if necessary.
3. Choose **5 — Show status** on both logins. The agent must be reachable and
   `local.echo.bot` should show `state = running`. On Echo, send a **new** message
   from your phone to the bot's Apple Account email. You should receive the
   agent's answer. Longer requests deliver a result when the work finishes.
   First installation starts with new messages, not old history.

If the updater is already working, the changes should arrive at its next
five-minute check. A dirty checkout is intentionally not auto-updated. The setup
file does not pull changes itself. Python **3.11+**, Git, and `imsg` in the Echo
login are required. Install `imsg` with `brew install steipete/tap/imsg` if missing.
Setup preserves the OpenAI model selection from your existing configuration.

The pairing file in `/Users/Shared/Echo-pairing-*.json` contains a secret and has
an ACL granting read access only to the designated Echo user in addition to its
creator. After successful pairing, its creator can delete it; both users already
have private copies of the necessary settings. Do not commit or send it in chat.

## Schedule reminders and agent work

Text Echo naturally, for example:

- “In two minutes, remind me to study AP Chem.”
- “Tomorrow at 7 PM, remind me to study and ask Codex to create a chemistry study guide.”
- “Every weekday at 8 AM, use my connected calendar to send me today's agenda.”
- “Show my schedules”, “Move my chemistry reminder to 8 PM”, or “Cancel my chemistry schedule”.

Echo has built-in tools to create, list, update, and cancel schedules. One-time
timers and recurring cron schedules persist across restarts in the main login's
private database. Echo confirms the saved time and timezone. It uses the Mac's
timezone unless `ECHO_TIMEZONE` is set in the main login's private `.env`.

Simple reminders send the saved text without a model call at firing time.
Scheduled work enters the normal agent workflow and its Decisions API checks,
with a result sent when it finishes. The Mac must be awake, online, and both
logins must remain signed in. Calendar/Codex jobs need their respective tools
configured; the bundled Codex worker starts local jobs, not cloud chats.
See [scheduling behavior and limits](docs/always-on.md#scheduling).

## Open the companion dashboard

On your **main Mac login**, open **scripts → Echo Dashboard.command**, or choose
**8 — Open dashboard** in the setup menu. It opens a private browser session in
the running agent. No separate web server or frontend installation is needed.

Browse and search 15 provider presets, connect accounts, check their status,
pause/remove connections, and view schedules and recent work. Gmail, Google
Calendar, and Drive use browser sign-in after a one-time Google app setup;
their bundled tools are **read-only**. Remote MCP providers expose the tools
allowed by their account permissions. Existing Codex/MCP configuration stays intact.

See [the connection guide](docs/connections.md) for Google setup, supported
providers, access details, and troubleshooting. Nothing needs changing on the
Echo login for the dashboard. Close its tab or launcher window whenever you like;
the background agent continues running.

## Add MCP connections manually from your main login

Choose **6 — Open MCP settings** in the setup menu. Configuration lives at:

```text
~/Library/Application Support/Echo/mcp.json
```

It supports multiple local **stdio** servers and remote **Streamable HTTP**
servers. For example:

```json
{
  "servers": {
    "my_local_tool": {
      "command": "/absolute/path/to/server",
      "args": ["--stdio"],
      "cwd": "/absolute/path/to/project"
    },
    "my_remote_tool": {
      "url": "https://your-server.example/mcp",
      "header_env": {"Authorization": "MY_MCP_AUTH"}
    },
    "echo_codex": {
      "enabled": false,
      "url": "http://127.0.0.1:8878/mcp",
      "header_env": {"Authorization": "ECHO_WORKER_AUTH"}
    }
  }
}
```

These are configuration examples, not installed Gmail integrations. Use your
chosen provider's actual command or URL and complete its authorization flow in
**your main login**. A stdio server can manage its own OAuth login. For HTTP
servers, Echo accepts headers supplied from environment variables; it does not
implement a generic OAuth browser flow or token refresh. A refreshing credential
provider/server is needed for unattended expiring tokens. Connections configured
inside ChatGPT or the Codex app are not automatically imported.

Store header values (including `Bearer ` when required) in the private file
`~/Library/Application Support/Echo/.env`, not in Git. Trusted local MCP commands
run with your main user's permissions. Remote MCP connections accept HTTP and HTTPS URLs, including query parameters. Only the configured phone sender can trigger tools; email,
project files and tool results are treated as data, not user authorization.

MCP configuration is read for each task execution. Unavailable servers are logged and
their tools omitted so ordinary chat can continue. Set `"required": true` on a
server if its failure should stop processing instead. After changing secret environment
values, choose **7 — Restart this login** on your main login. To check connections without invoking
any tools, run from the repository:

```sh
.venv/bin/python -m echo mcp-check
```

### Optional: keep Codex work on the Echo login

On Echo, open setup and choose **3 — Set up optional Codex worker**. Install Codex
CLI first if missing. Setup signs into Codex in that login if necessary and asks
for a working folder separate from the running Echo checkout. Then on your main
login choose **6** and change `echo_codex.enabled` to `true`.

Text “Ask Codex to create a hello.txt file saying hello.” If Echo requests approval,
review the saved tool arguments and reply with its `approve <code>` command. Echo tracks
the job and sends the final result automatically. Text `status`
while it works. The worker uses its own Codex login, one job at a time, a
30-minute timeout, and full filesystem access. It does not control existing
Codex desktop chats. Existing jobs survive an MCP restart; uncertain jobs are
not automatically repeated. Completion follow-ups require both the main agent
and Echo bridge to be running; queued notifications wait while the bridge is offline.

## Always running, without an idle model loop

- LaunchAgents start each process when its Mac user logs in and restart failures.
- The bridge uses `imsg watch` filesystem notifications, not continuous history polling.
- Incoming messages, task state, and outgoing notifications are saved in SQLite.
  One worker executes task steps; the message inbox remains available for status
  and cancellation. Waiting for Codex frees the worker for other tasks.
- Echo keeps one persistent conversation with you, including its delivered replies.
  Ordinary conversation and text revisions use one lightweight model call. Requests
  needing tools get a saved plan and one completion message.
  Follow-ups can update active work before its next action without starting a second task.
- The stronger planner is used for work requests; worker and notification models
  have separate settings. Code handles queueing, status, and ordinary completion
  delivery. There are **no idle OpenAI calls**.
- Agent tool actions run without a separate approval message by default. Echo still
  follows the user request and records each exact action and result. Set
  `ECHO_TOOL_CONFIRMATIONS=1` to opt into Decisions API review and approval messages.
- Codex completion is tracked automatically. Generic MCP servers do not all emit
  completion events; this does not install arbitrary webhooks, schedules, Canvas,
  Gmail, or browser automation for you.
- Stable message GUIDs make HTTP retries reuse existing work. Outgoing notification
  IDs prevent duplicate delivery attempts after a successful recorded send.
- A restart during active execution or a failed/interrupted iMessage send can leave
  an `uncertain` outcome. Echo does not repeat those external actions automatically.
  Exactly-once external effects and delivery cannot be guaranteed.
- Recent user/assistant exchanges, bounded completed-task summaries, and explicitly
  saved, source-dated memories help follow-ups. Chat history survives service restarts.
  Keyword retrieval is local and free. Task/chat records are separate from curated
  memory; `forget` does not erase those records.
- Limits bound model rounds, tool calls, tokens, and execution time. Optional dollar
  limits require verified model prices in your private configuration.

Read [the always-on guide](docs/always-on.md) for message commands, approval rules,
cost settings, persistence, and the current limitations.

**Both Mac users must remain logged in.** Fast User Switching and display lock
are fine. Logging either user out stops its part. After reboot, log into both
again. Keep the Mac powered, online, and awake; the bridge prevents idle sleep,
which does not guarantee operation with a closed laptop lid. This is not a
system daemon that can run Messages before any user logs in.

## Commands and status

Run commands from the repository with its own environment:

```sh
.venv/bin/python -m echo setup
.venv/bin/python -m echo status
.venv/bin/python -m echo restart
.venv/bin/python -m echo mcp-check
.venv/bin/python -m echo update
```

The `agent`, `bridge`, and `codex` commands run those processes in the foreground
for debugging. Don't start a duplicate of an installed process; singleton locks
reject it. `repair` and `diagnose` inspect Messages permissions/filtering.

State and secrets live under `~/Library/Application Support/Echo/`, independently
in each login. Logs live under `~/Library/Logs/Echo/`: `agent.log`, `bridge.log`,
`codex.log`, `update.log`, and the lifecycle journal `events.jsonl`. The status
command shows counts without displaying message contents or tokens. A `failed`
or `uncertain` item needs inspection and is not silently retried. Local message
queues, Codex outputs, and logs are not automatically rotated; they may contain
private data and must stay out of Git.

Both installed checkouts check `origin/main` every five minutes, validate the new
revision in isolation, install dependencies, and restart their own process.
Version 1 of the local message interface is kept compatible across rolling
updates. Use separate deployment checkouts: local changes intentionally block
updates. Lifecycle metadata is pushed to the **deployment-logs** branch if that
login has noninteractive GitHub write access. Message text, queues, MCP results,
and credentials are never included in that journal. An upload failure retains
local logs and retries later; never force-pushes.

## Repository layout

```text
echo/
  agent/       # coordinator, tasks/outbox, memory, budgets and agent inbox
  messaging/   # imsg validation, durable bridge, optional BlueBubbles transport
  tools/       # MCP client, local tool server, detached Codex jobs
  runtime/     # configuration, queue, setup, launchd, updates and diagnostics
scripts/       # all double-click commands
  Setup Echo.command
  Repair Echo.command
  Diagnose Echo.command
tests/         # offline unit and local protocol tests
docs/          # always-on operation and migration notes
pyproject.toml
requirements.txt
README.md
```

Three small root Python files (`run_echo.py`, `service.py`, `update_bot.py`) remain
as compatibility launchers so an existing installation can update itself without
breaking. Their implementation lives in `echo/runtime/`.

The split service uses `echo/agent/coordinator.py`; `processor.py` retains the
simple standalone/legacy response entry point. To add an MCP connection,
edit your main user's `mcp.json`; no code or Echo-login change is required. To add a
new built-in tool, register it in `echo/tools/mcp_server.py`.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use real SQLite, loopback HTTP, and MCP transports with fake model, messaging
and Codex execution. They incur no API charges and send no iMessages. They cover
authentication, sender filtering, idempotency, offline queuing, crash recovery,
unknown send outcomes, MCP authentication, idle behavior, task lifecycle, memory,
notification delivery, budgets, and legacy startup.

To test just a conversational response (uses the API, never sends an iMessage):

```sh
.venv/bin/python -m echo.agent.processor "Hello"
```

See [legacy installations](docs/legacy.md) for the former single-login mode.

### Tool selection and persistent preferences

Connected tool descriptions include why to use the service and the action's own
purpose. Canvas is associated with school, homework, assignments, grades, and
due dates, so requests such as “show my assignments” do not need an MCP name.
Custom `mcp.json` server entries can set `use_when` to explain their purpose;
provider presets supply guidance automatically. Account labels still distinguish
school and personal connections.

Echo has `rules_list`, `rules_save`, and `rules_delete` tools. Ask it to remember a
preference, change a rule, list your rules, or delete a rule by key. Saving with an
existing key replaces that rule. Rules are private to the sender, persist across
restarts, and are loaded on every conversation and worker model call, including
resumed work. They are separate from searchable task memories and never grant
authorization for external actions. Up to 50 rules of 2,000 characters each can
be saved. Tool requests deliver their result without a separate “I'll check”
acknowledgment; status and cancellation remain available while work is running.
