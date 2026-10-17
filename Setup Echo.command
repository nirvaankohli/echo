#!/bin/zsh
cd -- "${0:A:h}" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ ! -x .venv/bin/python ]]; then
  python3 -c 'import sys; assert sys.version_info >= (3,11)' || {
    print 'Install Python 3.11 or newer, then reopen this file.'
    read '?Press Enter to close.'
    exit 1
  }
  python3 -m venv .venv || exit 1
fi
if .venv/bin/python -m pip install -r requirements.txt; then
  .venv/bin/python -m echo setup
fi
read '?Press Enter to close.'
