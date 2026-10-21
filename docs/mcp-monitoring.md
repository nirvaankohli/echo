# Monitoring connected MCPs

Ask Echo to watch a particular connected account and state what deserves attention,
for example: “Every 15 minutes check my School calendar for new conflicts and ask
whether I want help resolving them.” The worker discovers and tests the available
read tools and saves its procedure with `monitor_create`. Discovery and the choice
of procedure are model-driven; Echo does not assume every connected service can be
monitored. Tool descriptions need to explain filtering, pagination, and timestamps.

Monitoring reuses the durable scheduler, account selection rules, action review,
budgets and iMessage outbox. Each occurrence receives saved JSON state for cursors
or snapshots. It returns updated state and findings. State, deduplication keys and
outgoing findings are committed together. Empty findings produce no completion
message. Cancelling a monitor prevents an in-flight check from publishing findings.
List or cancel monitors with the ordinary schedule tools. To change or restart a
monitor, cancel and recreate it. Failed checks pause the monitor and use the usual
failure notification; inspect Recent Work before restarting it.

Only tools classified as read candidates are offered during monitoring; their MCP
annotations are not proof of safety, so the existing action review still applies.
Annotate read tools with `readOnlyHint: true`, or configure an appropriate explicit
tool policy for a trusted server. Monitoring does not expose calendar writes,
external messaging or scheduler tools. A question in a finding is delivered to the
owner; any subsequent action is a separate user request.

This implementation polls on the saved cron schedule and uses model calls on each
check. It does not subscribe to remote events, automatically enable every connection,
or implement a separate quiet-hours/digest system. Use an appropriate schedule to
control frequency and time of day. Saved state is limited to 16 KB and each check to
20 findings. Use server-side change cursors for large sources. Do not advance a
cursor past unprocessed pages; an incomplete check must fail visibly.

## A calendar MCP that is easy to monitor

These are suggested application tool names and data fields, not special MCP methods:

- `list_calendars()` returns stable calendar IDs, names, account identity, timezone,
  and access level.
- `list_events(calendar_id, start, end, page_token)` returns a bounded, paginated
  time window. Document whether recurring events are expanded into occurrences.
- `get_event(calendar_id, event_id)` returns full details and a source link.
- `list_event_changes(calendar_id, cursor, page_token)` optionally returns new,
  updated and cancelled events, plus a next cursor after all pages are consumed.
  Specify first-call baseline behavior and return an explicit cursor-expired error
  with instructions for rebuilding the baseline when necessary.

Use MCP `inputSchema`, `outputSchema`, structured results, and accurate read-only
annotations. Include stable occurrence IDs, event version/updated time, title,
start/end (with UTC offsets and IANA timezone), all-day dates, cancellation status,
organizer, the current user's RSVP status, location, and a source URL. Preserve
cancellation records in the change feed. Do not use tool-list-changed notifications
to represent calendar changes: those mean the available tool definitions changed.

A result might contain:

```json
{
  "calendar_id": "school",
  "events": [{
    "id": "meeting-42:2026-10-23",
    "version": "3",
    "title": "Project review",
    "start": "2026-10-23T14:00:00-04:00",
    "end": "2026-10-23T15:00:00-04:00",
    "timezone": "America/New_York",
    "updated_at": "2026-10-20T20:15:00Z",
    "status": "confirmed",
    "response_status": "needsAction",
    "url": "https://calendar.example/events/meeting-42"
  }],
  "next_page_token": null,
  "next_cursor": "revision-103"
}
```

Return facts rather than instructions to contact the user. Echo decides whether a
change matters in context and can ask: “Your project review moved to 2 PM and now
overlaps your appointment. Want help rescheduling?” If you expose `update_event` or
`respond_to_invitation`, make them separate write tools with version preconditions
and idempotency keys; monitoring must not call them.

Protocol reference: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
