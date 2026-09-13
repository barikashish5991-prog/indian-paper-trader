#!/bin/zsh
cd -- "$(dirname -- "$0")"
echo 'Open http://127.0.0.1:8766 after the app starts.'
echo 'Keep this terminal open. Your Mac stays awake while this process runs.'
caffeinate -i python3 app.py serve --provider upstox --port 8766
read -r "reply?Press Enter to close."
