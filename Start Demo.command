#!/bin/zsh
cd -- "$(dirname -- "$0")"
python3 app.py demo --port 8765
read -r "reply?Press Enter to close."
