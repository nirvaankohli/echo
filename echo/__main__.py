"""One entry point for Echo's agent, bridge, tools and maintenance."""
import argparse
import logging
import os
import runpy


def main():
    parser = argparse.ArgumentParser(description='Echo background agent and Messages bridge')
    parser.add_argument('command', choices=['setup', 'agent', 'bridge', 'codex', 'status', 'update',
                                            'repair', 'diagnose', 'mcp-check', 'restart', 'dashboard'])
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
    try:
        if args.command == 'setup':
            from echo.runtime.setup import menu
            menu()
        elif args.command == 'agent':
            from echo.agent.server import run
            run()
        elif args.command == 'bridge':
            from echo.messaging.bridge import run
            run()
        elif args.command == 'codex':
            from echo.tools.worker import run
            run()
        elif args.command == 'dashboard':
            from echo.dashboard.launcher import open_dashboard
            open_dashboard()
        elif args.command == 'status':
            from echo.runtime.status import show
            show()
        elif args.command == 'restart':
            from echo.runtime.setup import restart
            restart()
        elif args.command == 'mcp-check':
            import asyncio
            from contextlib import AsyncExitStack
            from echo.runtime.config import load_env
            from echo.tools.client import configured_servers, connect
            load_env()
            async def check():
                failed = False
                async with AsyncExitStack() as stack:
                    for name, config in configured_servers().items():
                        try:
                            client = await connect(stack, config)
                            count = len((await client.list_tools()).tools)
                            print(f'{name}: connected, {count} tools available')
                        except Exception as exc:
                            failed = True
                            print(f'{name}: connection failed ({type(exc).__name__})')
                print('No tools executed.')
                if failed:
                    raise RuntimeError('MCP connection check failed')
            asyncio.run(check())
        else:
            module = 'update_bot' if args.command == 'update' else args.command
            runpy.run_module('echo.runtime.' + module, run_name='__main__')
    except (KeyboardInterrupt, EOFError):
        print('Stopped.')
    except Exception as exc:
        # Setup validation is intentionally user-facing. Other exceptions can contain secrets.
        if args.command == 'setup' and isinstance(exc, (ValueError, RuntimeError)):
            print(str(exc))
        else:
            logging.error('%s stopped (%s). Check setup/status and local logs.', args.command, type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
