"""AUD-26: warehouse one-shots must outlive systemd's 90s default."""

from pathlib import Path

REPO = Path(__file__).parents[2]
UNITS = (
    REPO / "warehouse" / "wikistream-export.service",
    REPO / "warehouse" / "wikistream-parity.service",
)


def test_warehouse_units_allow_a_long_run():
    for unit in UNITS:
        text = unit.read_text()
        assert "TimeoutStartSec=1800" in text, f"{unit.name} lacks a start timeout"
