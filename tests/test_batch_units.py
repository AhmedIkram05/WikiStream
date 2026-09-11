"""Batch-plane wiring contract tests (no CH needed).

The systemd side of the gap-2 fix (OnFailure -> wikistream-fail-notify@ -> Slack)
lives in unit text, which is exactly the kind of invariant that dies silently
in a "quick cleanup". These tests assert the contract: every batch oneshot
pages on failure, the template's plumbing matches the script's expectations,
and boot.sh actually installs the pieces. Greppable invariants, cheap to keep.
"""

import os
from pathlib import Path

REPO = Path(__file__).parents[1]
WAREHOUSE = REPO / "warehouse"

#: The four batch oneshot services that own timers (mirrors boot.sh's cp list).
BATCH_UNITS = [
    WAREHOUSE / "wikistream-export.service",
    WAREHOUSE / "wikistream-parity.service",
    WAREHOUSE / "wikistream-backup.service",
    REPO / "gx" / "wikistream-gx.service",
]


def test_all_batch_units_page_on_failure():
    for unit in BATCH_UNITS:
        assert "OnFailure=wikistream-fail-notify@%n.service" in unit.read_text(), (
            f"{unit.name} lost its OnFailure wiring"
        )


def test_fail_notify_template_contract():
    text = (WAREHOUSE / "wikistream-fail-notify@.service").read_text()
    assert "EnvironmentFile=/opt/wikistream/.env" in text, "webhook env source missing"
    assert "notify-failure.sh %i" in text, "template must pass the failed unit name"


def test_notify_failure_script_is_guarded():
    path = WAREHOUSE / "notify-failure.sh"
    assert os.access(path, os.X_OK), "notify-failure.sh must stay executable"
    text = path.read_text()
    assert "SLACK_WEBHOOK_URL missing" in text, "missing env must fail loudly"
    assert "--data-urlencode" in text, "payload must stay form-encoded"


def test_boot_installs_the_notify_template():
    boot = (REPO / "scripts" / "boot.sh").read_text()
    assert "wikistream-fail-notify@.service" in boot, "boot.sh dropped the template"
    assert "daemon-reload" in boot, "template needs a daemon-reload to be discoverable"


def test_backup_timer_daily_with_persistent_catchup():
    timer = (WAREHOUSE / "wikistream-backup.timer").read_text()
    assert "OnCalendar=*-*-* 06:20:00" in timer, (
        "backup cadence drifted off daily 06:20"
    )
    assert "Persistent=true" in timer, "daily backups lose the downtime catch-up"
    assert "hourly" not in timer.lower(), "stale hourly backup description left behind"
    header = (WAREHOUSE / "backup.sh").read_text()
    assert "daily ClickHouse" in header, "backup.sh header no longer states daily"
