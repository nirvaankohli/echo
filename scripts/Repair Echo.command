#!/bin/zsh
cd -- "${0:A:h:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'The Python environment is missing. Follow the README setup first.'
  read '?Press Enter to close.'
  exit 1
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
exec .venv/bin/python -m echo.runtime.repair
