"""L2: the data-quality systemd unit must not allocate a TTY."""

from pathlib import Path

REPO = Path(__file__).parents[2]
UNIT = REPO / "consumer" / "wikistream-data-quality.service"


def test_data_quality_run_disables_tty():
    text = UNIT.read_text()
    assert "run --rm -T data_quality" in text
    assert "run --rm data_quality" not in text
