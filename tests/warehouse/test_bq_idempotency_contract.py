"""AUD-02 live gate contract: real BigQuery merge/reload idempotency."""

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).parents[2]
SCRIPT = REPO / "warehouse" / "test_bq_idempotency.sh"


def test_bq_gate_uses_the_real_sql_and_replace_loads():
    script = SCRIPT.read_text()
    assert "sql/merge_edits.sql" in script
    assert "sql/reload_raw_sample.sql" in script
    assert script.count("--replace") >= 3
    assert 'bq --project_id="$BQ_TEST_PROJECT" rm -r -f -d "$DATASET"' in script
    assert "raw sample duplicated" in script


def test_bq_gate_is_wired_into_ci():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "bq-idempotency:" in ci
    assert "./warehouse/test_bq_idempotency.sh" in ci
    assert "BQ_TEST_PROJECT: ${{ secrets.TF_VAR_PROJECT_ID }}" in ci


def test_bq_gate_runs_with_a_stub_bq(tmp_path):
    stub = tmp_path / "bq"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "stdin = sys.stdin.read()\n"
        "with open(os.environ['BQ_LOG'], 'a') as f:\n"
        "    f.write(json.dumps({'args': args, 'stdin': stdin}) + '\\n')\n"
        "if 'query' not in args or '--format=json' not in args:\n"
        "    sys.exit(0)\n"
        "q = args[-1]\n"
        "state = os.environ['BQ_STATE']\n"
        "if 'kpi_edits_hourly' in q and 'COUNT(*)' in q:\n"
        "    try:\n"
        "        n = int(open(state).read() or 0)\n"
        "    except FileNotFoundError:\n"
        "        n = 0\n"
        "    open(state, 'w').write(str(n + 1))\n"
        "    print(json.dumps([{'c': 1, 'edits': 2 if n == 0 else 3}]))\n"
        "elif 'raw_events_sample' in q:\n"
        "    print(json.dumps([{'c': 1}]))\n"
    )
    stub.chmod(0o755)
    log = tmp_path / "bq.log"
    env = os.environ | {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "BQ_TEST_PROJECT": "test-project",
        "BQ_TEST_DATASET": "test_dataset",
        "BQ_LOG": str(log),
        "BQ_STATE": str(tmp_path / "state"),
    }
    proc = subprocess.run(
        [str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    loads = [r for r in rows if "load" in r["args"]]
    assert len(loads) == 4
    assert all("--replace" in r["args"] for r in loads)
    merges = [r["stdin"] for r in rows if "query" in r["args"] and r["stdin"]]
    assert any("test_dataset.kpi_edits_hourly" in sql for sql in merges)
    assert any("test_dataset.raw_events_sample" in sql for sql in merges)
    assert not any("wikistream." in sql for sql in merges)
