"""Compatibility entry point; all CLI behavior lives in run_echo.py."""

from run_echo import main

if __name__ == '__main__':
    raise SystemExit(main())
