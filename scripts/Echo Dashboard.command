#!/bin/zsh
cd -- "${0:A:h:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'Open Setup Echo.command on your main Mac login first.'
  read '?Press Enter to close.'
  exit 1
fi
.venv/bin/python -m echo dashboard
