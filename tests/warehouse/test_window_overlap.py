"""Trailing-overlap window contract for export.sh / parity.sh (no CH needed).

The late-arrival fix (implementation-log §9.10) lives in shell window math:
the hourly export covers a trailing 2-hour overlap and parity mirrors it, so
a late event (timestamped in the previous hour, ingested after the :00 run)
rehydrates into BigQuery on the next run and gets parity-checked on the same
slice. The MERGE/parity SQL is idempotent per window, but only if the two
scripts keep deriving the same window shape — hence these greppable tests.
"""

from pathlib import Path

REPO = Path(__file__).parents[2]
WAREHOUSE = REPO / "warehouse"

TOP_OF_HOUR_END = "END=\"$(date -u +'%Y-%m-%d %H:00:00')\""
TWO_HOURS_AGO_START = "START=\"$(date -u +'%Y-%m-%d %H:00:00' -d '2 hours ago')\""
ONE_HOUR_BACKFILL_MENTION = "-d '1 hour ago'"


def _script(name: str) -> str:
    return (WAREHOUSE / name).read_text()


def test_export_default_window_trails_two_hours():
    text = _script("export.sh")
    assert TWO_HOURS_AGO_START in text, "export default window lost the 2h overlap"
    assert TOP_OF_HOUR_END in text, "export window_end must stay top-of-hour"
    assert "trailing 2 completed UTC hours" in text, "usage text stale"
    assert ONE_HOUR_BACKFILL_MENTION not in text, "old '1 hour ago' default left behind"


def test_parity_default_window_mirrors_export():
    text = _script("parity.sh")
    assert TWO_HOURS_AGO_START in text, "parity stopped mirroring the export overlap"
    assert TOP_OF_HOUR_END in text, "parity window_end must stay identical to export's"
    assert "mirroring export.sh" in text, "parity header no longer states the mirror"
    assert ONE_HOUR_BACKFILL_MENTION not in text, "old '1 hour ago' default left behind"
