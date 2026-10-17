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

Conversation turns and background tasks are separate. A lightweight conversation
model decides whether to reply, start tool work, or continue an existing task.
Both it and the worker receive actual user/assistant exchanges, so “put that in a
document” can refer to Echo's previous draft. Completed-task follow-ups may create
a new internal work item with the same chat context; they do not reset the conversation.

Corrections invalidate outdated model proposals and not-yet-started approvals.
Changed tool arguments require a new approval where the tool policy requires one.
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

Tools marked read-only can be used without a separate approval. Unknown or
sensitive tools require approval unless the local configuration grants a specific
policy. A server's `tool_policy` maps exact MCP tool names to `read`,
`private_write`, or `approval`, for example:

```json
{
  "servers": {
    "documents": {
      "url": "https://your-provider.example/mcp",
      "header_env": {"Authorization": "DOCUMENTS_MCP_AUTH"},
      "tool_policy": {
        "get_document": "read",
        "create_private_document": "private_write",
        "share_document": "approval"
      }
    }
  }
}
```

These are illustrative names, not installed tools. Only grant `private_write` to
an audited tool whose scope you understand. A broad tool may also share or delete
content depending on its arguments. Tool descriptions, source documents, and
retrieved memories are data, not permission to take unrelated actions.

The built-in local Codex worker can write within its configured workspace. The
remote `echo_codex` connection requires approval by default. To authorize workspace
jobs without a new approval every time, explicitly add this policy inside that
trusted server's configuration:

```json
"tool_policy": {
  "codex_start": "private_write",
  "codex_status": "read",
  "codex_jobs": "read"
}
```

Only grant this to your actual Echo worker. Approval requests show the exact saved
arguments; requests too large to review in the message are refused instead of
silently approving hidden argument content. The worker's sandbox
and separate Codex authentication remain in force. Echo cannot undo already
completed actions by cancelling the task.

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
