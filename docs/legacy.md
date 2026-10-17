# Legacy single-login installations

An existing `local.echo.bot` LaunchAgent that invokes `run_echo.py` keeps working
through the compatibility launcher. The same is true for `service.py` and
`update_bot.py`. The runtime now lives under `echo/runtime/`; the updater can
therefore pull this reorganized repository before you run the split setup wizard.

To migrate, follow the main README: set up the agent in your main login first,
then pair the Messages bridge in the Echo login. Each wizard replaces only that
login's `local.echo.bot` and `local.echo.update`. The optional Codex worker is a
separate `local.echo.codex` LaunchAgent. Do not share a writable checkout between
users or copy a virtual environment between them.

The old `agent/` and `messaging/` import paths moved to `echo.agent` and
`echo.messaging`. External scripts importing these internals must update their
imports. `imsg_main.py` was retired; use `run_echo.py` for a legacy foreground run.
The old repair and diagnose shortcuts moved to `scripts/`; the root setup menu
also exposes them. `Enable Codex.command` was replaced by setup option 3.

## Optional BlueBubbles transport

It remains available for older setups, but has no access to personal MCP tools:
its localhost webhook is unauthenticated and is not a trusted tool trigger.
Configure `BLUEBUBBLES_URL`, `BLUEBUBBLES_PASSWORD`, and
`BLUEBUBBLES_SEND_METHOD=apple-script` in the repository `.env`, then run:

```sh
.venv/bin/python -m echo.messaging.bluebubbles
```

It binds to `127.0.0.1:8000` by default. Configure BlueBubbles to POST new-message
webhooks to `http://127.0.0.1:8000/webhook`. The receiver requires event type
`new-message`, text in `data.text`, sender in `data.handle.address`, and the
conversation GUID in `data.chats[0].guid`; it ignores `isFromMe` and reactions.
Sending uses `POST /api/v1/message/text` with `chatGuid`, `message`, `tempGuid`,
and `method`, with the BlueBubbles password in its query parameter.

The supported split architecture uses direct `imsg` instead of BlueBubbles.
