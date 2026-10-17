"""Echo's local MCP tools. Add new capabilities by registering another function."""

from dotenv import load_dotenv
from mcp.server import MCPServer

from agent.codex_jobs import ROOT, get_job, list_jobs, start_job

server = MCPServer('Echo tools')
server.tool(name='codex_start')(start_job)
server.tool(name='codex_status')(get_job)
server.tool(name='codex_jobs')(list_jobs)

if __name__ == '__main__':
    load_dotenv(ROOT / '.env')
    server.run(transport='stdio')
