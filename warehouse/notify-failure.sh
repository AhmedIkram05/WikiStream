#!/usr/bin/env bash
# OnFailure hook for the WikiStream batch oneshots (see wikistream-fail-notify@.service):
# a failed run only lands in the journal, and the freshness panel needs ~2h to
# turn red. This turns systemd's on-failure signal into an instant Slack page,
# so export/parity/backup/GX crashes page at :03, not at the next dashboard look.
set -euo pipefail

ENV_FILE="${WIKISTREAM_ENV_FILE:-/opt/wikistream/.env}"
if [ -z "${SLACK_WEBHOOK_URL:-}" ] && [ -f "$ENV_FILE" ]; then
  # Only the webhook is read; the unit deliberately does not load .env, so the
  # ClickHouse/Grafana secrets never enter this short-lived process.
  SLACK_WEBHOOK_URL="$(grep -E '^SLACK_WEBHOOK_URL=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
fi
: "${SLACK_WEBHOOK_URL:?SLACK_WEBHOOK_URL missing from ${ENV_FILE}}"
unit="${1:?usage: notify-failure.sh <failed-unit-name>}"
case "$unit" in
  *[!A-Za-z0-9@._:-]*)
    echo "[fail-notify] invalid unit name: ${unit}" >&2
    exit 2
    ;;
esac

payload="$(printf '{"text":":warning: WikiStream batch job FAILED: %s (check: journalctl -u %s)"}' "$unit" "$unit")"
curl -sf -m 10 -H 'Content-type: application/json' \
  --data "$payload" \
  "${SLACK_WEBHOOK_URL}" >/dev/null
echo "[fail-notify] Slack page sent for ${unit}"
