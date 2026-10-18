"""Provider presets, sourced from provider documentation. Accounts are never pre-connected."""


def provider(id, name, category, description, url='', auth='oauth', docs='', **extra):
    return dict(id=id, name=name, category=category, description=description,
                url=url, auth=auth, docs=docs, **extra)


CATALOG = [
    provider('gmail', 'Gmail', 'Everyday', 'Find emails and read conversations.', auth='google',
             docs='https://developers.google.com/workspace/gmail/api/quickstart/python', scope='gmail.readonly'),
    provider('google-calendar', 'Google Calendar', 'Everyday', 'See your calendars, events, and upcoming agenda.', auth='google',
             docs='https://developers.google.com/workspace/calendar/api/quickstart/python', scope='calendar.readonly'),
    provider('google-drive', 'Google Drive', 'Everyday', 'Find files and read Google Docs.', auth='google',
             docs='https://developers.google.com/workspace/drive/api/quickstart/python', scope='drive.readonly'),
    provider('notion', 'Notion', 'Knowledge', 'Search your workspace and work with pages.', 'https://mcp.notion.com/mcp',
             docs='https://developers.notion.com/guides/mcp/get-started-with-mcp'),
    provider('linear', 'Linear', 'Work', 'Follow issues, plan projects, and keep work moving.', 'https://mcp.linear.app/mcp',
             docs='https://linear.app/docs/mcp'),
    provider('github', 'GitHub', 'Developer', 'Explore repositories, issues, and pull requests.', 'https://api.githubcopilot.com/mcp/', auth='token',
             docs='https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md'),
    provider('atlassian', 'Jira & Confluence', 'Work', 'Bring projects, tickets, and team knowledge together.', 'https://mcp.atlassian.com/v2/mcp',
             docs='https://atlassian.github.io/atlassian-mcp-server/'),
    provider('asana', 'Asana', 'Work', 'Work with tasks and projects across your team.', 'https://mcp.asana.com/v2/mcp',
             docs='https://developers.asana.com/docs/connecting-mcp-clients-to-asanas-v2-server', client_required=True),
    provider('figma', 'Figma', 'Creative', 'Give Echo context from your designs.', 'https://mcp.figma.com/mcp',
             docs='https://developers.figma.com/docs/figma-mcp-server/'),
    provider('canva', 'Canva', 'Creative', 'Work with presentations and visual content.', 'https://mcp.canva.com/mcp',
             docs='https://www.canva.dev/docs/apps/mcp/verify-app/', note='Canva may require an approved client application.'),
    provider('webflow', 'Webflow', 'Creative', 'Connect your websites and content collections.', 'https://mcp.webflow.com/mcp',
             docs='https://developers.webflow.com/mcp/reference/getting-started'),
    provider('stripe', 'Stripe', 'Business', 'Explore your products, customers, and payments.', 'https://mcp.stripe.com',
             docs='https://docs.stripe.com/mcp'),
    provider('hubspot', 'HubSpot', 'Business', 'Connect your CRM and customer context.', 'https://mcp.hubspot.com',
             docs='https://developers.hubspot.com/docs/apps/developer-platform/build-apps/integrate-with-the-remote-hubspot-mcp-server', client_required=True),
    provider('sentry', 'Sentry', 'Developer', 'Investigate errors and application issues.', 'https://mcp.sentry.dev/mcp',
             docs='https://mcp.sentry.dev/'),
    provider('cloudflare', 'Cloudflare', 'Developer', 'Work with your Cloudflare projects and services.', 'https://mcp.cloudflare.com/mcp',
             docs='https://github.com/cloudflare/mcp'),
]
BY_ID = {item['id']: item for item in CATALOG}
