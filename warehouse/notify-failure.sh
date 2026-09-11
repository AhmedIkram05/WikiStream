#!/usr/bin/env bash
# OnFailure hook for the WikiStream batch oneshots (see wikistream-fail-notify@.service):
# a failed run only lands in the journal, and the freshness panel needs ~2h to
# turn red. This turns systemd's on-failure signal into an instant Slack page,
# so export/parity/backup/GX crashes page at :03, not at the next dashboard look.
set -euo pipefail

: "${SLACK_WEBHOOK_URL:?SLACK_WEBHOOK_URL missing from /opt/wikistream/.env (boot.sh section 5)}"
unit="${1:?usage: notify-failure.sh <failed-unit-name>}"

curl -sf -m 10 --data-urlencode \
  "payload={\"text\":\":warning: WikiStream batch job FAILED: ${unit} (check: journalctl -u ${unit})\"}" \
  "${SLACK_WEBHOOK_URL}" >/dev/null
echo "[fail-notify] Slack page sent for ${unit}"
