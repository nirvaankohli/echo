# Always-on Echo

The main Mac login runs the coordinator, models, memory, and personal MCP tools.
The Echo login runs the Messages bridge and optional Codex worker. Both logins
must remain signed in, and the Mac must remain awake and online. This design
waits for work; it does not run a model in a continuous thinking loop.

## Update and test

Update both clean deployment checkouts from `origin/main`. Installed updaters do
this on their next five-minute check. A locally edited checkout is deliberately
left alone. Existing services use the same per-user configuration and pairing;
you do not need to copy the OpenAI key to Echo or recreate accounts.

If you are installing for the first time, follow the main-login then Echo-login
setup steps in [README](../README.md). Open **scripts → Setup Echo.command** and
choose **5** to inspect status or **7** to restart that login's service. To change
models or limits, edit the **main login's** private file:

```text
~/Library/Application Support/Echo/.env
```

Restart the main service after editing environment values. To change MCP servers,
choose setup option **6** on the main login; connections are rediscovered for task
execution. The optional Codex worker must also be installed on Echo with option
**3**, signed into Codex, and enabled in the main login's `mcp.json`.

From your phone, send a new message to Echo's Apple Account email:

1. `Remember that I prefer concise study plans.`
2. `Show memory` — expect your saved preference.
3. `Ask Codex to create codex-test.txt containing Codex works.`
4. If asked, review the Codex request and reply with its `approve <code>` command. Then send `Status`
   to see the current task state while it works.
5. Wait for the final result without asking for another status update. Check the
   file in the configured Codex workspace on the Echo account.

To test conversational continuity, text `Draft a three-step biology study plan`,
wait for the reply, then text `Make the second step shorter`. Echo should revise
that plan without asking you to repeat it or announcing a new background task.
Restart the main service and ask `What plan were we discussing?` to check persistence.
No new setup on the Echo login is needed for conversation history; the main agent
creates and migrates its local tables automatically when updated.

This test performs a real Codex job and model calls. Offline repository tests
mock those actions and do not send messages or make paid requests.

## What you can text

| Message | Behavior |
| --- | --- |
| Ordinary chat or a text revision | Answer directly using the ongoing conversation; no background task or generic acknowledgment. |
| A request needing tools | Save a task, acknowledge it, plan and execute using available tools, then notify you of the result. |
| A correction to active work | Attach the original message to that task and apply it before the next action. Ask for clarification if the referent is ambiguous. |
| `status` | Show the latest relevant task state without a model call. |
| `cancel` or `stop` | Request cancellation of active work before the next action. Already-started external actions can still finish. |
| `remember <fact>` | Save an explicit, source-dated long-term memory. |
| `forget <phrase>` | Delete curated memories whose text or source contains that literal phrase. |
| `show memory` | List recent curated memories. |
| `approve <code>` | Approve the exact saved tool action identified in Echo's request. Plain `approve` works only when exactly one action is awaiting approval. |
| “Remind me tomorrow at 7 PM to study” | Save a one-time reminder, confirm the actual date/time/timezone, and text it when due. |
| “Every weekday at 8 AM, summarize my calendar” | Save a recurring job that runs with the connected tools when due. |
| “Show / move / cancel my chemistry schedule” | List, replace, or disable the saved schedule through tools. |

## Scheduling

The main-login coordinator exposes `schedule_create`, `schedule_list`,
`schedule_update`, and `schedule_cancel` alongside MCP tools. No calendar account,
system crontab, extra service, or setup on Echo is required. Each creation or
change goes through the Decisions API with the exact reminder, future prompt,
timing, and user's request. Work launched later gets its own action checks against
the saved authorization. Scheduled prompts cannot bypass those checks.

The conversation model receives the current date, time, and timezone and routes
schedule requests to the worker. The worker uses either an ISO 8601 `at` timestamp
with its UTC offset or a five-field `cron` expression with an IANA timezone, not
both. The default timezone comes from macOS; optionally set `ECHO_TIMEZONE` in
the main login's private `.env` and restart. Existing schedules retain their saved
timezone. Recurring schedules use [croniter](https://github.com/pallets-eco/croniter)
with timezone-aware dates. Ask Echo to clarify ambiguous times and check the
confirmed date/time before relying on a reminder.

The scheduler checks local state every five seconds without model calls. A due
reminder goes directly into the durable iMessage outbox; an optional work prompt
goes into the normal task queue. A reminder can be sent while another task is
running; scheduled agent work waits for the single worker to become available.
Codex completion uses the existing job monitor and sends a result afterward.
This does not add cloud Codex access: the bundled worker creates local jobs in
its configured workspace. Other capabilities require their connected tools.

Schedules and occurrence records live in `tasks.sqlite3` under the main login's
private Echo state directory. The due occurrence and next run are committed in
one transaction, so restarting does not enqueue the same occurrence twice.
After downtime, overdue one-time jobs run once; recurring jobs run at most one
catch-up occurrence, then advance to the next future time. A schedule with an
active occurrence skips overlapping ticks. Failed/uncertain work is not retried
automatically; future recurring occurrences still run. There are at most 50 active
schedules per owner, with a minimum cron resolution of one minute.

Updating or cancelling a schedule also cancels queued occurrences, occurrences
waiting for approval, and pending reminders. Already-running work or messages
already being delivered may finish.
Use “cancel my chemistry schedule” to disable future runs; bare `cancel` retains
its existing meaning of cancelling the active task. Schedules survive service
restarts, but they cannot wake a sleeping/offline Mac or run in a logged-out
session. After a reboot, both accounts must be signed in again.

To test from your phone, send “In two minutes, remind me to test Echo scheduling”.
Wait for the saved-time confirmation, then the reminder. Follow with “Show my
schedules” to inspect its last run, or test a recurring reminder and cancel it
by name. Repository tests exercise timers, restart recovery, cancellation,
timezone changes, decision checks, and Codex handoff without real messages.

## Conversation continuity

Conversation turns and background tasks are separate. A lightweight conversation
model decides whether to reply, start tool work, or continue an existing task.
Both it and the worker receive actual user/assistant exchanges, so “put that in a
document” can refer to Echo's previous draft. Completed-task follow-ups may create
a new internal work item with the same chat context; they do not reset the conversation.

Corrections invalidate outdated model proposals and not-yet-started approvals.
Changed tool arguments receive a new decision check, which may request approval.
A casual `yes` never approves a saved tool action. Already-started actions cannot
be undone: a running external Codex job finishes first, and its saved follow-up is
applied when Echo resumes. Echo tells you that limitation instead of starting a
duplicate job. There is one task execution worker and a separate conversation
worker; this is not a general parallel-agent scheduler.

## Tools and authorization

Configure Canvas, Google Docs, or browser tools on your main login before asking
Echo to use them. Echo cannot log into those accounts merely because their names
appear in a request. ChatGPT/Codex app connections are not imported automatically.
A native desktop automation tool may need the correct user session active and
unlocked; Fast User Switching is not a promise that every GUI tool will work.

Every new model-proposed tool action passes through the dedicated
[OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions).
The check receives the original user request, follow-ups, recent conversation,
main model plan, a brief action rationale, and the exact tool name and arguments.
The rationale is an explicit action justification, not hidden chain-of-thought.
The decision compares it with the actual request rather than treating the model's
claim of authorization as permission.

A valid `allow` decision runs immediately. Confidence is recorded for auditing;
there is no additional confidence cutoff that overrides an allow verdict.
The policy favors routine, reversible work within your request, including requested
file edits, bounded Codex jobs, and reminders. Ordinary implementation details do
not need separate permission. A flagged or uncertain verdict, refusal, malformed
response, unavailable API, or exhausted review budget still asks for confirmation.
The prompt checks scope, destructive changes, private data,
security changes, and external effects. A decision check cannot guarantee that
arbitrary delegated code or tools will behave safely; the Codex sandbox remains.

The approval message includes the concern and exact arguments. Reply with its
`approve <code>` to run that saved action, or cancel. A changed request invalidates
not-yet-started approvals and any in-flight decision. Successful action checkpoints
and exact human approvals are reused without buying another decision call.
Flagged arguments over 8,000 characters stop for a shorter proposal instead of
showing a truncated approval. The main model's rationale is stripped before
calling the MCP tool.

Existing `tool_policy` values (`read`, `private_write`, `approval`) remain compatible
but serve only as tool hints to the decision check. They neither automatically
approve nor automatically block new model-proposed actions. No MCP configuration
edit is required for this change. The built-in job monitor's status checks do not
make a decision API call on each poll.

The endpoint uses `gpt-6-luna` by default. The 2.x OpenAI SDK's authenticated HTTP
method calls `/decisions` directly, so this does not require upgrading the rest of
the SDK to 3.x. Decisions and main-model calls share the task and daily budget.
For a dollar cap, configure a separate `decisions/gpt-6-luna` entry in
`ECHO_MODEL_PRICES` using verified Decisions API rates, rather than Responses
rates for that model. Missing usage retains the conservative reservation.

Private `action_decisions` records in `tasks.sqlite3` retain the action's rationale,
revision, model verdict, confidence, and any error type. They join to the exact
arguments in `actions`; neither is published to the deployment log.

## Notifications and completion

Acknowledgments and promised final results use durable outgoing records. Ordinary
progress and unchanged heartbeats stay quiet. Other supported events can use the
small notification model to decide whether a message is significant. Promised
results are not discarded by that decision model. A failed optional decision retries
with backoff (60, 120, 240, then 480 seconds); after five failed decisions its event
is marked `deferred` for inspection, rather than repeatedly spending tokens. A
promised completion does not need this model call.

Codex jobs are tracked to completion and their outcome resumes the saved task so
Echo can produce a final answer. Status checks run only for active jobs. The bridge checks its local outgoing
queue every five seconds while idle, with no model call. `ECHO_JOB_WATCH_SECONDS`
defaults to 7200: after that tracking window, an unresolved job needs inspection;
it is not automatically relaunched. Other
MCP tools may return a final result immediately; a generic server returning its
own job ID needs a corresponding completion adapter before Echo can monitor it.
This release does not automatically subscribe to arbitrary MCP events, install
Canvas webhooks, schedule recurring work, or assemble notification digests.

A message saying a tool started is not proof of completion. Echo's model is
instructed to verify artifacts through available tools and include links, but
verification depends on the tools actually exposing a way to inspect the result.

## Memory and privacy

Full local chat records include incoming text and replies confirmed sent by the
bridge. They survive restart. Existing tasks and confirmed outgoing replies are
migrated automatically. Pending or uncertain replies are supplied separately as
operational state; Echo should not assume you have read them.
The last 40 completed requests from the older stateless service are also imported
as reference context. That protocol had no delivery receipts, so its generated
answers are retained as task results, not confirmed assistant messages. No old
requests or replies are automatically sent again.

Each model turn receives a bounded recent window (up to 40 prior messages within
24,000 characters including the current message), preserving whole messages and
their user/assistant roles. Older chat is retained locally but is not all uploaded
on every request. This is bounded conversation context, not unlimited recall.
Long-term memory includes explicit
`remember` requests and bounded summaries of completed non-social tasks: the first
600 characters of the request plus the first 1800 characters of the outcome.
Those summaries require no extra model call and let older results remain searchable
outside the recent-task window. They are excerpts, not a complete semantic summary.

Memory stores text, source, creation date, last update, and optional expiration. SQLite full-text retrieval works offline. Source-key replacement and
expiration are available in the memory API for integrations that maintain changing
facts; Echo does not automatically reconcile every remembered deadline with Canvas.

The `Memory` API also accepts an injected OpenAI-compatible embedding client for
semantic retrieval. This is a developer extension: the default service does not
configure one or buy embeddings for each message. There is no external vector
database or automatic raw-document ingestion.

`forget` removes the selected curated text, vector if present, and search entry.
It does **not** delete past task records, conversation context, Codex outputs,
provider-side data, backups, or your iMessage history. Saying “forget biology” is
not an account-wide privacy erasure. Local state has no automatic retention limit.

Private state lives under `~/Library/Application Support/Echo/` in each user.
Treat task databases, memories, tool results, and Codex output as private. Do not
commit them. Logs in `~/Library/Logs/Echo/` primarily describe operational states;
the deployment journal publishes lifecycle metadata only, not message content
or API credentials. Credential-looking memories are rejected as a guardrail;
never intentionally put secrets into memory.

## Cost and limits

The defaults are configuration choices, not a quoted monthly price:

| Setting | Default | Purpose |
| --- | --- | --- |
| `OPENAI_PLANNER_MODEL` | `gpt-4.1` | Plan work requests. |
| `OPENAI_WORKER_MODEL` | `gpt-4.1-mini` | Execute task steps and produce answers. |
| `OPENAI_CONVERSATION_MODEL` | Worker model | Answer ordinary chat and route work/follow-ups in one call. |
| `OPENAI_DECISION_MODEL` | `gpt-6-luna` | Review each new tool action through the Decisions API. |
| `OPENAI_NOTIFICATION_MODEL` | `gpt-4.1-mini` | Judge supported non-routine events. |
| `ECHO_MAX_ROUNDS` | `8` | Maximum worker response rounds per task. |
| `ECHO_MAX_TOOL_CALLS` | `12` | Maximum tool actions per task. |
| `ECHO_TASK_SECONDS` | `600` | Active execution segment time limit; not a wall-clock guarantee for an external job. |
| `ECHO_JOB_WATCH_SECONDS` | `7200` | Maximum tracking window for an external job. |
| `ECHO_TASK_TOKEN_LIMIT` | `64000` | Input plus output allowance per chat turn; new tool work shares its originating turn's allowance. |
| `ECHO_DAILY_TOKEN_LIMIT` | `250000` | Shared daily token allowance. |
| `ECHO_DAILY_USD_LIMIT` | `0` | Optional dollar cap; zero disables the dollar cap. |
| `ECHO_MODEL_PRICES` | `{}` | Verified input/output rates in USD per million tokens, keyed by model. |

These limits apply to model calls made by the installed main agent, including
legacy message requests during rolling upgrades. Standalone CLI/legacy bot runs
outside that service do not share these limits.

Token reservations are conservative before each call and reconciled to reported
usage afterward. Interrupted calls retain their reservation. Each reservation keeps
the configured prices used for that call. A limit can stop
work earlier than its actual cost would require, which is preferable to silently
exceeding it. Daily accounting uses UTC days.

To enable a dollar cap, set your chosen nonzero cap and verified rates for **every
model used**. Each model entry has numeric `input` and `output` rates. Do not copy
an unverified price example. Missing or invalid rates prevent model calls under
a dollar cap. Enabling a cap after earlier same-day usage with unknown prices is
blocked rather than counting that earlier spending as zero. These are configured
estimates, not a live billing feed; changes
to provider prices require updating your configuration. Codex account usage and
fees charged by external tools are separate from these OpenAI API estimates.

## Restarts and uncertain outcomes

Tasks, plans, job references, action checkpoints, usage, and outgoing messages
persist across restarts. Waiting jobs can be checked again. A process dying in
the middle of an external action can leave an unknown outcome; Echo marks it
uncertain instead of replaying the action and possibly creating a second document
or sending a second message.

Outgoing sends have the same limitation: a crash after iMessage sends but before
recording success cannot prove delivery. Echo avoids automatic duplicate sends,
which means some uncertain notifications need manual inspection. Exactly-once
external actions and delivery are not guaranteed.

Use setup option **5** and the main `agent.log` / Echo `bridge.log` when something
stops. Inspect the external tool's result before resubmitting uncertain work.
