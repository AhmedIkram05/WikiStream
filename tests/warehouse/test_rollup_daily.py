"""SQL-shape contract for warehouse/sql/rollup_daily.sql (no BigQuery needed).

The daily gold rollup derives day-over-day growth with a LAG window over a
daily aggregate. BigQuery owns the execution, so — like test_window_overlap.py
— this pins the two behaviours that are easy to regress and invisible to the
export-side parity tests:

1. The scan starts at MIDNIGHT of the day before {START} (not {START} - 1 day),
   so a mid-day {START} still gives LAG the FULL preceding calendar day. A
   {START}-1day scan would contribute only the prior day's tail hours and
   produce a wrong dod_growth_pct.
2. LAG() partitions by wiki and orders by day, and dod_growth_pct is
   SAFE_DIVIDE(edits - prev_day_edits, prev_day_edits) with no gap fill (a
   wiki's zero-edit days have no row — deliberate, see the SQL comment).

Only the window days are merged, so re-runs keep the boundary row correct.
"""

from pathlib import Path

REPO = Path(__file__).parents[2]
ROLLUP = REPO / "warehouse" / "sql" / "rollup_daily.sql"


def _sql() -> str:
    return ROLLUP.read_text()


def test_scan_starts_at_midnight_of_prior_day():
    sql = _sql()
    # Full prior calendar day, not a rolling {START} - 1 day window.
    assert "DATE_SUB(DATE(TIMESTAMP('{START}')), INTERVAL 1 DAY)" in sql, (
        "prior-day scan must start at midnight of the day before {START}"
    )
    assert "INTERVAL 1 DAY" in sql
    # The window end is unchanged: {END} bounds the merged rows.
    assert "AND hour < TIMESTAMP('{END}')" in sql


def test_lag_is_per_wiki_ordered_by_day():
    assert (
        "LAG(edits) OVER (PARTITION BY wiki ORDER BY day) AS prev_day_edits" in _sql()
    ), "LAG must partition by wiki and order by day"


def test_dod_growth_uses_safe_divide():
    sql = _sql()
    assert (
        "SAFE_DIVIDE(edits - prev_day_edits, prev_day_edits) AS dod_growth_pct" in sql
    ), "dod_growth_pct must guard the divide (first day / zero prev)"
    # Gap semantics are a deliberate choice, documented in-file (no fill).
    assert "no fill" in sql


def test_only_window_days_are_merged():
    sql = _sql()
    assert (
        "WHERE day >= DATE(TIMESTAMP('{START}')) AND day <= DATE(TIMESTAMP('{END}'))"
        in sql
    ), "only window days may reach the MERGE (keeps re-runs idempotent)"
    assert "ON T.day = S.day AND T.wiki = S.wiki" in sql, "MERGE key drifted"
