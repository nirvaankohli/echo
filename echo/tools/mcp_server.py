"""Echo's local MCP tools. Add new capabilities by registering another function."""

from mcp.server import MCPServer
from echo.runtime.config import load_env

from echo.tools.codex_jobs import ROOT, get_job, list_jobs, start_job

server = MCPServer('Echo tools')
server.tool(name='codex_start')(start_job)
server.tool(name='codex_status')(get_job)
server.tool(name='codex_jobs')(list_jobs)

if __name__ == '__main__':
    load_env()
    server.run(transport='stdio')
