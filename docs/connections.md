# Connections dashboard

The dashboard runs inside the main login's existing agent at
`http://127.0.0.1:8877/app/`. Open **scripts → Echo Dashboard.command** to sign in,
or choose **8 — Open dashboard** in **scripts → Setup Echo.command**. From the
repository, the equivalent command is:

```sh
.venv/bin/python -m echo dashboard
```

The launcher creates a short-lived, single-use browser sign-in link. An ordinary
bookmark works while the eight-hour browser session remains valid. Reopen the
launcher when it expires. The agent must already be installed and running; use
Setup Echo's status/restart options if the page cannot load. No Node build,
separate server, cloud hosting, or Echo-login setup is required.

Open the dashboard launcher rather than the repository’s `index.html`. The HTML
file shows an opening guide when viewed directly; account connections require the
running agent.

The dashboard is local to this Mac, not a website accessible from your phone.
Both macOS logins still need to remain signed in for the agent and iMessage
bridge to work. Closing the dashboard or its Terminal launcher does not stop them.

## Connect Google once, then sign in

Google requires your own OAuth application for this self-hosted installation.
Echo cannot supply a universal Google client or reuse a Codex/ChatGPT connection.

1. In [Google Cloud](https://console.cloud.google.com/), create or select a project.
   Enable **Gmail API**, **Google Calendar API**, and/or **Google Drive API** in
   the API library for the services you want.
2. Configure the Google Auth Platform consent screen. For a personal external
   app in Testing, add your Google email under test users. Configure the requested
   read-only scopes if Google asks for them: `gmail.readonly`, `calendar.readonly`,
   and `drive.readonly` under `https://www.googleapis.com/auth/`.
3. Create an OAuth client of type **Web application**. Add the exact redirect URI
   shown under **Settings → Google app setup**. With the default agent port:
   `http://127.0.0.1:8877/app/oauth/google/callback`.
4. Download the client credentials JSON. Import it in dashboard Settings, or
   enter the client ID and secret there, then click **Save Google setup**. The
   file is parsed in your browser; only those two values are saved locally.
5. Return to Connections, choose Gmail, Calendar, or Drive, and click **Continue
   with Google**. Select your account and review the requested access. Each
   service is connected separately; you can name multiple accounts, such as
   “School Gmail” and “Personal Gmail”.

Echo verifies access with a provider API request before marking it Connected.
The native tools can search/read emails, list calendars/events, find Drive files,
and read native Google Docs. They cannot send mail, change calendar events, edit
documents, or read arbitrary binary attachments. Google access tokens refresh
automatically while the grant remains valid. External apps in Testing can have
seven-day refresh tokens for these scopes; reconnect if Google expires the grant.
Changing your OAuth app credentials may require reconnecting existing accounts.
See [Google's OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server)
and [refresh-token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).

## Provider catalog

These are connection presets for real provider endpoints, not preauthorized
accounts. Availability, tool coverage, administrator approval, and paid-plan
requirements are controlled by each provider. Each detail panel links its setup
documentation. Account sign-in must finish successfully before Echo uses it.

| Service | Connection |
| --- | --- |
| Gmail | Google OAuth app; native read-only tools |
| Google Calendar | Google OAuth app; native read-only tools |
| Google Drive | Google OAuth app; native read-only tools |
| Notion | Remote MCP; browser sign-in |
| Linear | Remote MCP; browser sign-in |
| GitHub | Remote MCP; personal access token with the permissions you choose |
| Jira & Confluence | Atlassian remote MCP; browser sign-in |
| Asana | Remote MCP; your OAuth app client ID/secret and browser sign-in |
| Figma | Remote MCP; browser sign-in |
| Canva | Remote MCP; may require an approved client application |
| Webflow | Remote MCP; browser sign-in |
| Stripe | Remote MCP; browser sign-in |
| HubSpot | Remote MCP; your OAuth app client ID/secret and browser sign-in |
| Sentry | Remote MCP; browser sign-in |
| Cloudflare | Remote MCP; browser sign-in |

For providers requiring an OAuth app, register the callback shown in the panel
(`http://127.0.0.1:8877/app/oauth/mcp/callback` by default). Use **Advanced: use your
own OAuth app** when the provider does not allow dynamic client registration.
If its OAuth policy does not support this client, follow that provider's setup
guide; a catalog entry does not guarantee every account can authenticate.

**Custom connection** accepts other HTTPS Streamable HTTP MCP endpoints with
OAuth, a bearer token, or no authentication. This is not a generic website URL
and does not support legacy SSE-only servers. Local stdio servers and the Echo
Codex worker remain configured through `mcp.json` using setup option 6.

## What the controls mean

- **Connected:** the last connection check succeeded. The panel shows when it
  was checked and how many tools were discovered. This is not continuous health
  monitoring; click **Check** to verify again.
- **Configured:** an existing manual `mcp.json` entry. The dashboard preserves
  it and does not claim it has been verified. Edit it through setup option 6.
- **Pause / Resume:** control discovery for future tasks. Pausing does not undo
  work or stop an already-running tool call. Checking a paused account leaves it
  paused. Resume verifies it before enabling.
- **Reconnect:** repeat sign-in if a grant expired or setup failed.
- **Remove:** delete this account's local credentials and configuration. It does
  not revoke a provider-side grant; use the provider's account settings for that.

The next agent task discovers newly connected tools without an agent restart.
Echo still sends proposed tool actions through its Decisions API checks.
Provider tool descriptions and returned content are not user instructions.
The Schedules and Activity pages display saved schedules and recent work;
create or change schedules by texting Echo.

## Privacy and troubleshooting

Connections and credentials live in the main user's private
`~/Library/Application Support/Echo/connections.sqlite3`, separate from the
repository and from the bridge pairing token. The file is readable/writable only
by that macOS user. Credentials are stored locally, not encrypted by the app;
protect the Mac account and its backups. Tokens/client secrets are not returned
by dashboard state APIs or sent to the model. API results can enter the agent's
context when it uses a tool. Do not commit or share the database or credential
files.

The dashboard requires a separate browser session, exact loopback host, and
same-origin mutation requests. OAuth uses state checks and PKCE; callbacks must
return to the browser session that started the flow. Account connection attempts
expire after five minutes and do not survive an agent restart. Start again if
the updater restarted the agent during sign-in.

If sign-in opens a blocked popup, use the **Open sign-in page** link. Keep the
same browser/profile for the provider callback. For a failed Google check,
confirm the selected API is enabled, your test-user entry exists, and the
redirect URI exactly matches. For another provider, check its linked setup guide,
account plan, and client-registration requirements. A failed check leaves the
account unavailable to new agent tasks until it verifies successfully.

To test after connecting, text Echo “Summarize my unread emails” or “What is on
my calendar today?” A green connection check verifies authentication and tool
discovery; an actual message tests the complete iMessage-to-agent workflow.
