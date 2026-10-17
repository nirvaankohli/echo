#!/bin/zsh
cd -- "${0:A:h:h}" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ ! -x .venv/bin/python ]]; then
  print 'The Python environment is missing. Follow README setup.'
  read '?Press Enter to close.'
  exit 1
fi
exec .venv/bin/python -m echo.runtime.diagnose
