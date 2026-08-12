"""MV equivalence suite for migrations/004-006 (plan §3.2.2, ADR-006).

Encodes the ADR-006 spot-check guarantee: each materialized view over
default.raw_events must equal the equivalent raw GROUP BY over the same
window. Runs against a live ClickHouse exactly like tests/migrations —
standalone helpers here (do NOT import from that module). Every test is
@pytest.mark.ch; each starts from reset() + apply_ok() for isolation.

SummingMergeTree may return unmerged duplicate rows, so BOTH sides of every
comparison are aggregated SUM()s over one shared cutoff literal — never row
counts.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]

#: The six |length delta| buckets (labels are the 006 contract). The
#: ground-truth counts below are independent of all SQL: they catch a
#: boundary typo shared by BOTH copies of the multiIf.
BUCKETS = [
    "0",
    "1-10",
    "11-100",
    "101-1000",
    "1001-10000",
    "10000+",
]
BUCKET_COUNTS = {
    "0": "2",
    "1-10": "2",
    "11-100": "2",
    "101-1000": "2",
    "1001-10000": "1",
    "10000+": "1",
}

#: Edge matrix — (type, wiki, title, is_bot, length{new[, old]}).
#: 12 rows inserted, 10 reach the MVs (log + empty-wiki excluded). Deltas
#: probe the bucket boundaries exactly (10 -> '1-10', 11 -> '11-100') so a
#: <=/< typo in BOTH copies of the multiIf still changes a classified row.
FIXTURE = [
    ("edit", "enwiki", "Main_Page", False, {"new": 100, "old": 100}),  # delta 0 -> '0'
    (
        "edit",
        "enwiki",
        "Main_Page",
        True,
        {"new": 100, "old": 100},
    ),  # delta 0 -> '0' (bot split)
    ("new", "frwiki", "New_Page", False, {"new": 5, "old": 0}),  # delta 5 -> '1-10'
    (
        "edit",
        "dewiki",
        "Edge_Low",
        False,
        {"new": 110, "old": 100},
    ),  # delta 10 -> '1-10' (boundary)
    (
        "edit",
        "frwiki",
        "Main_Page",
        False,
        {"new": 150, "old": 100},
    ),  # delta 50 -> '11-100' (same title, other wiki)
    (
        "edit",
        "ptwiki",
        "Edge_High",
        False,
        {"new": 211, "old": 200},
    ),  # delta 11 -> '11-100' (just over boundary)
    (
        "edit",
        "enwiki",
        "Shrink_Me",
        False,
        {"new": 50, "old": 500},
    ),  # delta -450 -> '101-1000' (shrinking)
    (
        "edit",
        "enwiki",
        "Big_Page",
        False,
        {"new": 6000, "old": 1000},
    ),  # delta 5000 -> '1001-10000'
    (
        "new",
        "fawiki",
        "Huge_Title",
        True,
        {"new": 60000, "old": 10000},
    ),  # delta 50000 -> '10000+'
    (
        "log",
        "enwiki",
        "Log_Entry",
        False,
        {"new": 10, "old": 5},
    ),  # excluded: type 'log'
    ("edit", "", "Empty_Wiki", False, {"new": 200, "old": 100}),  # excluded: wiki = ''
    ("new", "enwiki", "NoOld_New", False, {"new": 500}),  # no length.old in JSON
]

#: Raw-side twin of the 006 bucket expression (token-identical, checked
#: against migrations/006_mv_edit_sizes_per_minute.sql in the test).
BUCKET_MULTIIF = (
    "multiIf("
    "abs(toInt64(length_new) - toInt64(length_old)) = 0, '0', "
    "abs(toInt64(length_new) - toInt64(length_old)) <= 10, '1-10', "
    "abs(toInt64(length_new) - toInt64(length_old)) <= 100, '11-100', "
    "abs(toInt64(length_new) - toInt64(length_old)) <= 1000, '101-1000', "
    "abs(toInt64(length_new) - toInt64(length_old)) <= 10000, '1001-10000', "
    "'10000+')"
)


def ch_env():
    return {
        "CH_HOST": os.environ.get("CH_HOST", "localhost"),
        "CH_PORT": os.environ.get("CH_PORT", "8123"),
        "CH_USER": os.environ.get("CH_USER", "wikistream"),
        "CH_PASSWORD": os.environ.get("CH_PASSWORD", "wikistream_dev_password"),
    }


def run_apply(env=None):
    """Run migrations/apply.sh; returns the CompletedProcess (caller asserts rc)."""
    e = {**os.environ, **ch_env()}
    if env:
        e.update(env)
    return subprocess.run(
        ["bash", str(REPO_ROOT / "migrations" / "apply.sh")],
        env=e,
        capture_output=True,
        text=True,
        check=False,
    )


def query(sql: str) -> str:
    """Run SQL via curl against the ClickHouse HTTP API; return stdout on success."""
    e = ch_env()
    out = subprocess.run(
        [
            "curl",
            "-sS",
            "--fail-with-body",
            "-H",
            f"X-ClickHouse-User: {e['CH_USER']}",
            "-H",
            f"X-ClickHouse-Key: {e['CH_PASSWORD']}",
            "-X",
            "POST",
            "--data-binary",
            sql,
            f"http://{e['CH_HOST']}:{e['CH_PORT']}/",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise AssertionError(
            f"SQL failed (curl {out.returncode}): "
            f"{out.stderr.strip() or out.stdout.strip()}"
        )
    return out.stdout


def scalar(sql: str) -> str:
    return query(sql).strip()


def sql_literal(s: str) -> str:
    return "'" + s.replace("'", "\\'") + "'"


def reset():
    # 3A's reset() only drops source tables; the MV storages (from the
    # pre-suite apply) must go too or the equivalence reads stale parts.
    # Drop MVs BEFORE their source so no dependency blocks the drop.
    for t in (
        "mv_edits_per_minute",
        "mv_top_pages_per_minute",
        "mv_edit_sizes_per_minute",
        "raw_events",
        "raw_events_v1",
    ):
        query(f"DROP TABLE IF EXISTS default.{t}")
    query("DROP TABLE IF EXISTS default.schema_migrations")


def apply_ok():
    r = run_apply()
    assert r.returncode == 0, f"apply.sh exited {r.returncode}:\n{r.stdout}\n{r.stderr}"
    return r


def event_json(type_, wiki, title, is_bot, length):
    ev = {
        "type": type_,
        "wiki": wiki,
        "title": title,
        "user": "U-" + title,
        "bot": is_bot,
        "timestamp": "2026-08-11T10:00:00Z",
    }
    if length is not None:
        ev["length"] = length
    return json.dumps(ev)


def fixture_totals():
    """(edits, bytes_delta) the 8 included FIXTURE rows must produce."""
    edits = 0
    bytes_delta = 0
    for type_, wiki, _title, _is_bot, length in FIXTURE:
        if type_ not in ("edit", "new") or wiki == "":
            continue
        edits += 1
        # mirror JSONExtractUInt(..., 'old') defaulting to 0 when absent
        bytes_delta += length.get("new", 0) - length.get("old", 0)
    return edits, bytes_delta


def mv_where(cutoff):
    """MV-side window filter: `minute >= '<cutoff>'` in parse-safe form.

    The cutoff literal is IDENTICAL on both sides (see raw_where); only the
    parse wrapper differs, so the window is deterministic across minute
    boundaries.
    """
    return f"(minute >= parseDateTimeBestEffort({sql_literal(cutoff)}))"


def raw_where(cutoff):
    """Raw-side window filter carrying the MV's canonical row filters."""
    return (
        "(event_type IN ('edit','new') AND wiki != ''"
        " AND inserted_at >= toStartOfMinute(parseDateTime64BestEffort("
        f"{sql_literal(cutoff)})))"
    )


def insert_fixture():
    """Insert every FIXTURE row at the live minute (toDateTime64(now(), 3)).

    Returns the window cutoff literal captured BEFORE the insert, which both
    the MV and the raw queries interpolate identically.
    """
    cutoff = scalar("SELECT toString(toStartOfMinute(now()))")
    rows = ",\n".join(
        f"(toDateTime64(now(), 3), {sql_literal(event_json(*row))})" for row in FIXTURE
    )
    query(f"INSERT INTO default.raw_events (inserted_at, event) VALUES\n{rows}")
    return cutoff


@pytest.mark.ch
def test_mv_tables_exist():
    reset()
    apply_ok()
    tables = set(query("SHOW TABLES LIKE 'mv_%'").split())
    assert tables == {
        "mv_edits_per_minute",
        "mv_top_pages_per_minute",
        "mv_edit_sizes_per_minute",
    }, tables


@pytest.mark.ch
def test_mv_edits_per_minute_equivalence():
    reset()
    apply_ok()
    cutoff = insert_fixture()

    n, b = fixture_totals()
    want = f"{n}\t{b}"  # 8\t55105
    mv = scalar(
        "SELECT sum(edits), sum(bytes_delta) FROM default.mv_edits_per_minute"
        f" WHERE {mv_where(cutoff)}"
    )
    raw = scalar(
        "SELECT count(), sum(toInt64(length_new) - toInt64(length_old))"
        f" FROM default.raw_events WHERE {raw_where(cutoff)}"
    )
    assert mv == raw == want, f"totals: MV {mv!r}, raw {raw!r}, want {want!r}"

    # per is_bot split (same wiki+minute, both bot values)
    mv_bot = scalar(
        "SELECT is_bot, sum(edits), sum(bytes_delta)"
        f" FROM default.mv_edits_per_minute WHERE {mv_where(cutoff)}"
        " GROUP BY is_bot ORDER BY is_bot"
    )
    raw_bot = scalar(
        "SELECT is_bot, count(), sum(toInt64(length_new) - toInt64(length_old))"
        f" FROM default.raw_events WHERE {raw_where(cutoff)}"
        " GROUP BY is_bot ORDER BY is_bot"
    )
    want_bot = "0\t8\t5126\n1\t2\t50000"
    assert mv_bot == raw_bot == want_bot, (
        f"is_bot split: MV {mv_bot!r}, raw {raw_bot!r}, want {want_bot!r}"
    )


@pytest.mark.ch
def test_mv_top_pages_per_minute_equivalence():
    reset()
    apply_ok()
    cutoff = insert_fixture()

    mv = scalar(
        "SELECT title, wiki, sum(edits) FROM default.mv_top_pages_per_minute"
        f" WHERE {mv_where(cutoff)} GROUP BY title, wiki ORDER BY title, wiki"
    )
    raw = scalar(
        "SELECT title, wiki, count() FROM default.raw_events"
        f" WHERE {raw_where(cutoff)} GROUP BY title, wiki ORDER BY title, wiki"
    )
    want = (
        "Big_Page\tenwiki\t1\n"
        "Edge_High\tptwiki\t1\n"
        "Edge_Low\tdewiki\t1\n"
        "Huge_Title\tfawiki\t1\n"
        "Main_Page\tenwiki\t2\n"
        "Main_Page\tfrwiki\t1\n"
        "New_Page\tfrwiki\t1\n"
        "NoOld_New\tenwiki\t1\n"
        "Shrink_Me\tenwiki\t1"
    )
    assert mv == raw == want, f"(title, wiki): MV {mv!r}, raw {raw!r}, want {want!r}"

    # same title on two wikis MUST remain two distinct rows (composite key
    # (minute, title, wiki) must not collapse across wikis)
    assert "Main_Page\tenwiki\t2" in mv
    assert "Main_Page\tfrwiki\t1" in mv


@pytest.mark.ch
def test_mv_edit_sizes_per_minute_equivalence():
    reset()
    apply_ok()
    cutoff = insert_fixture()

    # token guard: the raw-side expression IS the migration's multiIf
    text = (REPO_ROOT / "migrations" / "006_mv_edit_sizes_per_minute.sql").read_text()
    assert re.sub(r"\s+", "", BUCKET_MULTIIF) in re.sub(r"\s+", "", text), (
        "BUCKET_MULTIIF drifted from migrations/006_mv_edit_sizes_per_minute.sql"
    )

    for bucket, expected in BUCKET_COUNTS.items():
        mv = scalar(
            "SELECT sum(edits) FROM default.mv_edit_sizes_per_minute"
            f" WHERE {mv_where(cutoff)} AND bucket = {sql_literal(bucket)}"
        )
        raw = scalar(
            f"SELECT count() FROM default.raw_events WHERE {raw_where(cutoff)}"
            f" AND {BUCKET_MULTIIF} = {sql_literal(bucket)}"
        )
        assert mv == raw == expected, (
            f"bucket {bucket}: MV {mv!r}, raw {raw!r}, want {expected!r}"
        )


@pytest.mark.ch
def test_mv_excludes_log_and_empty_wiki():
    reset()
    apply_ok()
    cutoff = insert_fixture()

    n, _ = fixture_totals()
    inserted = scalar("SELECT count() FROM default.raw_events")
    included = scalar(
        f"SELECT count() FROM default.raw_events WHERE {raw_where(cutoff)}"
    )
    assert (inserted, included) == (str(len(FIXTURE)), str(n)), (inserted, included)

    # every MV row is a canonical (edit|new, wiki != '') row, so each MV's
    # total must equal the included raw count — never the full insert
    for tbl in (
        "mv_edits_per_minute",
        "mv_top_pages_per_minute",
        "mv_edit_sizes_per_minute",
    ):
        total = scalar(f"SELECT sum(edits) FROM default.{tbl} WHERE {mv_where(cutoff)}")
        assert total == str(n), f"{tbl}: total {total} != included {n}"

    # the wiki-bearing MVs must hold no '' wiki at all
    for tbl in ("mv_edits_per_minute", "mv_top_pages_per_minute"):
        empty = scalar(
            f"SELECT count() FROM default.{tbl} WHERE {mv_where(cutoff)} AND wiki = ''"
        )
        assert empty == "0", f"{tbl} holds an empty-wiki row"


@pytest.mark.ch
def test_interval_window_forms():
    """Grafana dashboard feeds `WHERE minute >= now() - INTERVAL ${window}`
    with window values '1 hour'/'24 hour'. DEV (3.2.3, implementation-log):
    ClickHouse 26.3 rejects the plan's `1h`/`24h` shorthand (Code 47
    UNKNOWN_IDENTIFIER, verified), so the dashboard variable ships full
    unit names. The query succeeding IS the assertion (a rejected INTERVAL
    fails scalar())."""
    scalar("SELECT now() - INTERVAL 1 hour")
    scalar("SELECT now() - INTERVAL 24 hour")


@pytest.mark.ch
def test_warehouse_export_sql_empty_safe():
    """One-source-of-truth contract (Q7, 3C pre-hook): export_*.sql in
    warehouse/sql must run and agree with the MV it sources. 3B has no
    warehouse/ — the skip path executes; 3C dropping the files in activates
    the comparison branch (their {START}/{END} placeholders are substituted
    here with a fixed empty range)."""
    exports = sorted(REPO_ROOT.glob("warehouse/sql/export_*.sql"))
    if not exports:
        pytest.skip("(3C not landed; export SQL empty-safe)")

    tables = set(query("SHOW TABLES").split())
    # CI's analytics-tests job boots a pristine ClickHouse (no live stream) and
    # this module collects before tests/warehouse seeds any data: export_*.sql
    # run over the full range below would return zero rows and trip the
    # non-empty assertion. Seed one synthetic edit (feeds raw_events AND all
    # three MVs via the materialized views) until the deterministic 10% sipHash
    # sample hits, so every export file is non-empty regardless of order.
    # Each attempt must VARY: sipHash64(event) is deterministic, so the same
    # content either always passes the 10% sample or never does. Bounded so a
    # pathological hash run cannot hang CI; ~10 iterations expected.
    seed_index = 0
    while (
        seed_index < 300
        and scalar(
            "SELECT count() FROM default.raw_events WHERE sipHash64(event) % 100 < 10"
        )
        == "0"
    ):
        seed_index += 1
        seed = {
            "type": "edit",
            "wiki": "enwiki",
            "title": f"3C_Seed_{seed_index}",
            "user": "3c-seed",
            "bot": False,
            "timestamp": "2026-08-12T10:00:00Z",
            "length": {"new": 10, "old": 5},
        }
        query(
            "INSERT INTO default.raw_events (inserted_at, event) VALUES "
            f"(toDateTime64(now(), 3), {sql_literal(json.dumps(seed))})"
        )
    for path in exports:
        sql = path.read_text()
        # export.sh/parity.sh substitute these placeholders (plan 3.3.3);
        # run with a deterministic empty range so 3C files land untouched.
        sql = re.sub(r"\{START\}", "1970-01-01 00:00:00", sql)
        sql = re.sub(r"\{END\}", "2299-12-31 00:00:00", sql)
        out = query(sql)  # raises on invalid SQL / missing table
        assert out.strip(), f"{path.name} returned no rows"
        for tbl in re.findall(r"FROM\s+(?:default\.)?(mv_\w+)", sql, re.IGNORECASE):
            assert tbl in tables, f"{path.name} selects missing table {tbl}"
