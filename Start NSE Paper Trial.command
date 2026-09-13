#!/bin/zsh
cd "$(dirname "$0")"
python3 app.py serve --provider nse --config hosted-config.json --port 8767
