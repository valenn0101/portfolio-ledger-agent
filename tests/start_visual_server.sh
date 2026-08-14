#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
nohup python3 app.py --no-browser </dev/null >/tmp/portfolio-ledger-agent.log 2>&1 &
echo "$!" >/tmp/portfolio-ledger-agent.pid
