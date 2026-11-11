"""Black-box behavior tests for the critical shell jobs (no cloud, no CH).

The batch .sh files carry live logic — window derivation, bq orchestration,
the export_runs JSON contract, fail-loud exit semantics (the OnFailure paging
chain depends on non-zero exits), and backup pruning that deletes data. The
SQL and unit text have their own contract tests; these check the SHELL
behavior by PATH-shimming the five externals (date/docker/bq/gcloud/gsutil)
with deterministic fakes, so the scripts run end-to-end on any host without
touching real systems. parity.sh is a documented skip: its log dir
(/var/log/wikistream) is unwritable on non-root dev hosts.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
WAREHOUSE = REPO / "warehouse"
FIXED_NOW = "2026-09-10 10:00:00"  # fake `date` clock, pinned top-of-hour

SHELL_SCRIPTS = [
    WAREHOUSE / "export.sh",
    WAREHOUSE / "parity.sh",
    WAREHOUSE / "backup.sh",
    WAREHOUSE / "notify-failure.sh",
    REPO / "scripts" / "boot.sh",
]

_DATE_STUB = f"""#!/usr/bin/env python3
import datetime, sys
fmt, offset = "%Y-%m-%d %H:00:00", 0
for i, a in enumerate(sys.argv):
    if a.startswith("+"):
        fmt = a[1:]
    if a == "-d":
        nxt = sys.argv[i + 1]
        offset = -2 if "2 hours" in nxt else (-1 if "1 hour" in nxt else 0)
dt = datetime.datetime.strptime("{FIXED_NOW}", "%Y-%m-%d %H:%M:%S") + \
    datetime.timedelta(hours=offset)
print(dt.strftime(fmt))
"""

_DOCKER_STUB = """#!/usr/bin/env python3
import os, re, sys
argv = " ".join(sys.argv)
log = os.environ.get("STUB_LOG")
if log:
    with open(log, "a") as fh:
        fh.write("docker " + argv + "\\n")
if "--format JSONEachRow" in argv:
    sys.stdin.read()
    for _ in range(3):
        print('{"hour":"2026-09-10T09:00:00Z","wiki":"enwiki"}')
elif "clickhouse-client" in argv and "BACKUP" in argv:
    sys.stdin.read()
    m = re.search(r"Disk\\('backups','([^']+)'\\)", argv)
    name = m.group(1) if m else "backup-unknown"
    os.makedirs(os.path.join(os.environ["CH_DATA_DIR"], "backups", name), exist_ok=True)
    print(f"20260910 10:00:01. {name} to Disk('backups','{name}') (BACKUP_CREATED)")
else:
    sys.stdin.read()
"""

_CMD_STUB = """#!/usr/bin/env python3
import os, sys
log = os.environ.get("STUB_LOG")
if log:
    with open(log, "a") as fh:
        fh.write("{cmd} " + " ".join(sys.argv[1:]) + "\\n")
if os.environ.get("STUB_{cmd}_FAIL".upper()) == "1":
    sys.stderr.write("stub {cmd}: injected failure\\n")
    sys.exit(1)
print("ok")
"""

_HEAD_STUB = """#!/usr/bin/env python3
# GNU-head semantics (the batch scripts use `head -n -K`, unsupported by BSD
# head — fine on the VM/CI runners, shimmed here so dev boxes behave the same).
import sys
args = sys.argv[1:]
k = 10
if "-n" in args:
    k = int(args[args.index("-n") + 1])
lines = sys.stdin.readlines()
if k > 0:
    lines = lines[:k]
elif k < 0:
    lines = lines[:k]
sys.stdout.writelines(lines)
"""


class _ShellHarness:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        stub_bin = tmp_path / "stubbin"
        stub_bin.mkdir()
        self.log = tmp_path / "stub.log"
        for name in ("gcloud", "gsutil"):
            (stub_bin / name).write_text(_CMD_STUB.replace("{cmd}", name))
            (stub_bin / name).chmod(0o755)
        for name, text in {
            "date": _DATE_STUB,
            "docker": _DOCKER_STUB,
            "bq": _CMD_STUB.replace("{cmd}", "bq"),
            "head": _HEAD_STUB,
        }.items():
            (stub_bin / name).write_text(text)
            (stub_bin / name).chmod(0o755)

    def run(
        self,
        script: Path,
        *args: str,
        fail_bq: bool = False,
        fail_gcloud: bool = False,
    ):
        env = os.environ.copy()
        env["PATH"] = str(self.tmp / "stubbin") + os.pathsep + env["PATH"]
        env["CLICKHOUSE_PASSWORD"] = "stub-pass"
        env["STAGING_BUCKET"] = "gs://stub-export-staging"
        env["BACKUP_BUCKET"] = "gs://stub-backups"
        env["STAGING_TMP"] = str(self.tmp / "staging")
        env["CH_DATA_DIR"] = str(self.tmp / "data")
        env["STUB_LOG"] = str(self.log)
        if fail_bq:
            env["STUB_BQ_FAIL"] = "1"
        if fail_gcloud:
            env["STUB_GCLOUD_FAIL"] = "1"
        return subprocess.run(
            ["bash", str(script), *args],
            env=env,
            text=True,
            capture_output=True,
            cwd=str(WAREHOUSE),
            timeout=120,
        )


@pytest.fixture()
def shell(tmp_path: Path) -> _ShellHarness:
    return _ShellHarness(tmp_path)


def test_bash_syntax_across_all_shell_scripts():
    for script in SHELL_SCRIPTS:
        proc = subprocess.run(
            ["bash", "-n", str(script)], capture_output=True, text=True
        )
        assert proc.returncode == 0, f"{script.name}: {proc.stderr}"


def test_export_default_trailing_two_hour_window(shell):
    proc = shell.run(WAREHOUSE / "export.sh")
    assert proc.returncode == 0, proc.stderr
    runs_path = shell.tmp / "staging" / "export_runs" / "2026091010.jsonl"
    row = json.loads(runs_path.read_text().strip())
    assert row["window_start"] == "2026-09-10T08:00:00Z"
    assert row["window_end"] == "2026-09-10T10:00:00Z"
    assert row["status"] == "success"
    assert row["rows_edits"] == row["rows_top_pages"] == row["rows_sizes"] == 3
    assert row["rows_raw_sample"] == 3
    log = shell.log.read_text()
    # 9 bq calls: 4 export loads + 3 merges + 1 reload + 1 export_runs load
    # (merges run as `bq --quiet query`, so count "query" on bq lines only —
    # docker lines legitimately carry --query flags since the M2
    # stdin-password fix moved the CH SQL out of stdin and into --query)
    assert log.count("bq load") == 5
    assert (
        sum(line.count("query") for line in log.splitlines() if line.startswith("bq"))
        == 4
    )
    assert "--schema=schemas/kpi_edits_hourly.json" in log
    assert "wikistream.kpi_edits_hourly_staging" in log


def test_export_overlapping_windows_replace_staging(shell):
    """AUD-02: the trailing 2-hour window re-exports the overlap hour. Each
    staging load must replace the previous run's rows, not append duplicate
    source keys that make BigQuery's MERGE abort. export_runs stays append."""
    first = shell.run(
        WAREHOUSE / "export.sh", "2026-09-10 08:00:00", "2026-09-10 10:00:00"
    )
    second = shell.run(
        WAREHOUSE / "export.sh", "2026-09-10 09:00:00", "2026-09-10 11:00:00"
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr

    loads = [
        line
        for line in shell.log.read_text().splitlines()
        if line.startswith("bq load")
    ]
    staging = [line for line in loads if "_staging" in line]
    runs = [line for line in loads if "export_runs" in line]
    assert len(staging) == 8  # 4 tables x 2 overlapping runs
    assert all("--replace" in line for line in staging)
    assert len(runs) == 2
    assert all("--replace" not in line for line in runs)


def test_export_password_never_in_docker_argv(shell):
    """M2: the ClickHouse password travels via stdin (`--password` with no
    value, like backup.sh) — never in docker/clickhouse-client argv, where
    any OS-login user could read it via ps. The stub logs every argv."""
    proc = shell.run(WAREHOUSE / "export.sh")
    assert proc.returncode == 0, proc.stderr
    log = shell.log.read_text()
    assert "clickhouse-client" in log  # the calls happened
    assert "stub-pass" not in log, "password leaked into a child-process argv"


def test_export_cleans_local_jsonl_after_success(shell):
    """AUD-03: the extracted JSONL must not accumulate in /tmp after a run."""
    proc = shell.run(WAREHOUSE / "export.sh")
    assert proc.returncode == 0, proc.stderr
    staging = shell.tmp / "staging"
    assert not list(staging.glob("kpi_*/*.jsonl"))
    assert not list(staging.glob("raw_sample/*.jsonl"))
    # The tiny run record is retained for the local contract assertions.
    assert (staging / "export_runs" / "2026091010.jsonl").exists()


def test_export_cleans_local_jsonl_on_bq_failure(shell):
    """A failed load must not leave the current JSONL behind either."""
    proc = shell.run(WAREHOUSE / "export.sh", fail_bq=True)
    assert proc.returncode != 0
    staging = shell.tmp / "staging"
    assert not list(staging.glob("kpi_*/*.jsonl"))
    assert not list(staging.glob("raw_sample/*.jsonl"))


def test_export_loads_when_gcs_backup_copy_fails(shell):
    """AUD-13: the GCS backup is non-critical; load/merge must run first and a
    copy failure must not abort the warehouse export."""
    proc = shell.run(WAREHOUSE / "export.sh", fail_gcloud=True)
    assert proc.returncode == 0, proc.stderr
    assert "backup copy failed" in proc.stderr
    log = shell.log.read_text()
    assert log.index("bq load") < log.index("gcloud storage cp")


def test_export_explicit_window_passthrough(shell):
    proc = shell.run(
        WAREHOUSE / "export.sh", "2026-09-05 05:00:00", "2026-09-05 06:00:00"
    )
    assert proc.returncode == 0, proc.stderr
    assert "2026-09-05T05:00:00Z..2026-09-05T06:00:00Z" in proc.stdout


def test_export_fails_loud_on_bq_failure(shell):
    proc = shell.run(WAREHOUSE / "export.sh", fail_bq=True)
    assert proc.returncode != 0, "bq failure must exit non-zero (OnFailure wiring)"


def test_backup_guard_prune_before_snapshot_and_keep_last_two(shell):
    backups = shell.tmp / "data" / "backups"
    backups.mkdir(parents=True)
    for day in range(5, 10):
        (backups / f"backup-2026090{day}-000000").mkdir()
    proc = shell.run(WAREHOUSE / "backup.sh")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # ordering contract: guard prune (oldest only) < BACKUP < lift < final prune
    assert out.index("guard-pruned") < out.index("created")
    assert out.index("created") < out.index("lifted") < out.index("[backup] pruned")
    assert "BACKUP_CREATED" in out
    # keep-last-2: the fresh snapshot + newest seeded dir survive
    assert sorted(p.name for p in backups.iterdir()) == [
        "backup-20260909-000000",
        "backup-20260910-100000",
    ]
    # M2 (backup.sh set the pattern): password via stdin, never in argv.
    assert "stub-pass" not in shell.log.read_text()
