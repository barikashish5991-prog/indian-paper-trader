#!/bin/zsh
cd -- "$(dirname -- "$0")"
python3 app.py token
read -r "reply?Press Enter to close."
