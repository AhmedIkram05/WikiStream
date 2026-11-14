"""Batch-plane wiring contract tests (no CH needed).

The systemd side of the gap-2 fix (OnFailure -> wikistream-fail-notify@ -> Slack)
lives in unit text, which is exactly the kind of invariant that dies silently
in a "quick cleanup". These tests assert the contract: every batch oneshot
pages on failure, the template's plumbing matches the script's expectations,
and boot.sh actually installs the pieces. Greppable invariants, cheap to keep.
"""

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).parents[1]
WAREHOUSE = REPO / "warehouse"

#: The four batch oneshot services that own timers (mirrors boot.sh's cp list).
BATCH_UNITS = [
    WAREHOUSE / "wikistream-export.service",
    WAREHOUSE / "wikistream-parity.service",
    WAREHOUSE / "wikistream-backup.service",
    REPO / "consumer" / "wikistream-data-quality.service",
]


def test_all_batch_units_page_on_failure():
    for unit in BATCH_UNITS:
        assert "OnFailure=wikistream-fail-notify@%n.service" in unit.read_text(), (
            f"{unit.name} lost its OnFailure wiring"
        )


def test_fail_notify_template_contract():
    text = (WAREHOUSE / "wikistream-fail-notify@.service").read_text()
    assert "EnvironmentFile=/opt/wikistream/.env" not in text, (
        "notifier must not inherit ClickHouse/Grafana secrets"
    )
    assert "notify-failure.sh %i" in text, "template must pass the failed unit name"


def test_notify_failure_script_is_guarded():
    path = WAREHOUSE / "notify-failure.sh"
    assert os.access(path, os.X_OK), "notify-failure.sh must stay executable"
    text = path.read_text()
    assert "SLACK_WEBHOOK_URL missing" in text, "missing env must fail loudly"
    assert "Content-type: application/json" in text, "Slack needs a JSON body"
    assert "--data-urlencode" not in text, "form-encoded payloads are not Slack JSON"
    assert '{"text":' in text, "JSON payload lost its text field"


def _notify_env(tmp_path: Path) -> tuple[dict, Path]:
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "payload = argv[argv.index('--data') + 1]\n"
        "json.loads(payload)\n"
        "open(os.environ['STUB_LOG'], 'w').write(payload)\n"
    )
    curl.chmod(0o755)
    log = tmp_path / "payload.json"
    env = os.environ | {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "SLACK_WEBHOOK_URL": "https://example.invalid/hook",
        "STUB_LOG": str(log),
    }
    return env, log


def test_notify_failure_sends_json_payload(tmp_path):
    env, log = _notify_env(tmp_path)
    proc = subprocess.run(
        [str(WAREHOUSE / "notify-failure.sh"), "wikistream-export.service"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(log.read_text())
    assert "wikistream-export.service" in payload["text"]


def test_notify_failure_reads_only_the_slack_secret(tmp_path):
    env, log = _notify_env(tmp_path)
    env.pop("SLACK_WEBHOOK_URL", None)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GCP_PROJECT=test-project\nSLACK_WEBHOOK_URL=https://example.invalid/hook\n"
    )
    env["WIKISTREAM_ENV_FILE"] = str(env_file)
    proc = subprocess.run(
        [str(WAREHOUSE / "notify-failure.sh"), "wikistream-export.service"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(log.read_text())["text"].endswith(
        "(check: journalctl -u wikistream-export.service)"
    )


def test_notify_failure_rejects_unsafe_unit_name(tmp_path):
    env, log = _notify_env(tmp_path)
    proc = subprocess.run(
        [str(WAREHOUSE / "notify-failure.sh"), 'bad"unit'],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert not log.exists()


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
