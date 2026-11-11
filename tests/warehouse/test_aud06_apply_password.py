"""AUD-06: apply.sh must never put the ClickHouse password in curl argv."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).parents[2]

_STUB = """#!/usr/bin/env python3
import os, stat, sys
argv = sys.argv[1:]
with open(os.environ["CURL_LOG"], "a") as fh:
    fh.write("ARGV " + " ".join(argv) + "\\n")
    if "--config" in argv:
        cfg = argv[argv.index("--config") + 1]
        fh.write("MODE " + oct(stat.S_IMODE(os.stat(cfg).st_mode)) + "\\n")
        with open(cfg) as fch:
            fh.write("CONFIG " + fch.read().replace("\\n", "|") + "\\n")
"""


def test_apply_password_never_in_curl_argv(tmp_path):
    stubbin = tmp_path / "bin"
    stubbin.mkdir()
    curl = stubbin / "curl"
    curl.write_text(_STUB)
    curl.chmod(0o755)

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_test.sql").write_text("SELECT 1;\n")

    log = tmp_path / "curl.log"
    env = os.environ.copy()
    env["PATH"] = str(stubbin) + os.pathsep + env["PATH"]
    env["CURL_LOG"] = str(log)
    env["CH_PASSWORD"] = "stub-pass"
    env["MIGRATIONS_DIR"] = str(migrations)

    proc = subprocess.run(
        ["bash", str(REPO / "migrations" / "apply.sh")],
        env=env,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr

    lines = log.read_text().splitlines()
    assert any(line.startswith("ARGV ") for line in lines)
    assert all("stub-pass" not in line for line in lines if line.startswith("ARGV "))
    assert "MODE 0o600" in lines
    assert any("X-ClickHouse-Key: stub-pass" in line for line in lines)
