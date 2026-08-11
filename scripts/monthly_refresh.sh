#!/bin/bash
# Monthly refresh of the justice directory: re-run all six family scrapers,
# commit and push whatever changed. Directory data drifts slowly, so monthly
# is enough. Runs via launchd (com.gperes.data-pt-justice-directory.monthly).
set -u
REPO="$HOME/promulga/data-pt-justice-directory"
LOG_DIR="$HOME/Library/Logs/data-pt-justice-directory"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/refresh-$(date +%Y%m%d-%H%M%S).log"
export SSL_CERT_FILE="$HOME/.local/share/scrape-portugal-legal/ca-bundle.pem"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "$REPO" || exit 1

{
    echo "=== refresh started $(date) ==="
    python3 scrape.py || echo "WARN: citius scrape failed"
    for f in scrapers/*.py; do
        python3 "$f" || echo "WARN: $f failed"
    done
    if [ -n "$(git status --porcelain)" ]; then
        git add -A
        git commit -m "Monthly refresh: $(date '+%Y-%m-%d')"
        git push origin main || echo "WARN: push failed"
    else
        echo "no changes"
    fi
    echo "=== refresh finished $(date) ==="
} >> "$LOG" 2>&1
