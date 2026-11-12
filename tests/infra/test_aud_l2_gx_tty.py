"""L2: the gx systemd unit must not allocate a TTY."""

from pathlib import Path

REPO = Path(__file__).parents[2]
UNIT = REPO / "gx" / "wikistream-gx.service"


def test_gx_run_disables_tty():
    text = UNIT.read_text()
    assert "run --rm -T gx" in text
    assert "run --rm gx" not in text
