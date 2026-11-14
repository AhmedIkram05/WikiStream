"""L1: export and parity must serialize on a shared lock, not just After=."""

from pathlib import Path

REPO = Path(__file__).parents[2]
LOCK = "/run/wikistream-warehouse.lock"
UNITS = {
    "wikistream-export.service": "/opt/wikistream/warehouse/export.sh",
    "wikistream-parity.service": "/usr/bin/python3 /opt/wikistream/warehouse/parity.py",
}


def test_warehouse_jobs_hold_the_same_flock():
    for unit, command in UNITS.items():
        text = (REPO / "warehouse" / unit).read_text()
        assert f"ExecStart=/usr/bin/flock -w 3600 {LOCK} {command}" in text
        assert f"ExecStart={command}\n" not in text
