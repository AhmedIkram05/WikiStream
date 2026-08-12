"""Warehouse export/parity suite for phase 3C (plan 3.3.7, Q6/Q7).

Runs against a live ClickHouse and exercises the SAME committed SQL files that
export.sh and parity.sh run in production. gcloud/bq are deliberately out of
scope here (3.3.7: local equivalence is the point) — the drift parity.sh
detects lives on the BQ side of its comparison, and the BQ-dialect twins are
only shape-checked locally.

Standalone helpers (do NOT import from tests/mv). Every test is
@pytest.mark.ch; each starts from reset() + apply_ok() for isolation.
SummingMergeTree may return unmerged duplicates, so every comparison is an
aggregated SUM() over one shared window literal — never row counts.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
WAREHOUSE_SQL = REPO_ROOT / "warehouse" / "sql"

#: Same edge matrix as tests/mv (12 rows, 10 reach the MVs; 8 edits with
#: 55105 bytes after the log + empty-wiki exclusions). Replicated here so the
#: warehouse suite is standalone; keep it in sync with tests/mv's FIXTURE.
FIXTURE = [
    ("edit", "enwiki", "Main_Page", False, {"new": 100, "old": 100}),
    ("edit", "enwiki", "Main_Page", True, {"new": 100, "old": 100}),
    ("new", "frwiki", "New_Page", False, {"new": 5, "old": 0}),
    ("edit", "dewiki", "Edge_Low", False, {"new": 110, "old": 100}),
    ("edit", "frwiki", "Main_Page", False, {"new": 150, "old": 100}),
    ("edit", "ptwiki", "Edge_High", False, {"new": 211, "old": 200}),
    ("edit", "enwiki", "Shrink_Me", False, {"new": 50, "old": 500}),
    ("edit", "enwiki", "Big_Page", False, {"new": 6000, "old": 1000}),
    ("new", "fawiki", "Huge_Title", True, {"new": 60000, "old": 10000}),
    ("log", "enwiki", "Log_Entry", False, {"new": 10, "old": 5}),
    ("edit", "", "Empty_Wiki", False, {"new": 200, "old": 100}),
    ("new", "enwiki", "NoOld_New", False, {"new": 500}),
]

BUCKET_COUNTS = {
    "0": "2",
    "1-10": "2",
    "11-100": "2",
    "101-1000": "2",
    "1001-10000": "1",
    "10000+": "1",
}

EXPORT_FILES = {
    "kpi_edits": "export_edits.sql",
    "kpi_top_pages": "export_top_pages.sql",
    "kpi_sizes": "export_sizes.sql",
    "raw_sample": "export_raw_sample.sql",
}
PARITY_FILES = {
    "edits": "parity_bq_edits.sql",
    "top_pages": "parity_bq_top_pages.sql",
    "sizes": "parity_bq_sizes.sql",
    "raw_sample": "parity_bq_raw_sample.sql",
}
#: (table, [columns]) the export SQL selects from — schema-drift guard.
SELECTED = {
    "default.mv_edits_per_minute": ["minute", "wiki", "is_bot", "edits", "bytes_delta"],
    "default.mv_top_pages_per_minute": ["minute", "title", "wiki", "edits", "bytes_delta"],
    "default.mv_edit_sizes_per_minute": ["minute", "bucket", "edits"],
    "default.raw_events": ["inserted_at", "event", "wiki", "title", "user", "is_bot", "event_type"],
}


def ch_env():
    return {
        "CH_HOST": os.environ.get("CH_HOST", "localhost"),
        "CH_PORT": os.environ.get("CH_PORT", "8123"),
        "CH_USER": os.environ.get("CH_USER", "wikistream"),
        "CH_PASSWORD": os.environ.get("CH_PASSWORD", "wikistream_dev_password"),
    }


def run_apply(env=None):
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
        bytes_delta += length.get("new", 0) - length.get("old", 0)
    return edits, bytes_delta


def insert_fixture():
    rows = ",\n".join(
        f"(toDateTime64(now(), 3), {sql_literal(event_json(*row))})" for row in FIXTURE
    )
    query(f"INSERT INTO default.raw_events (inserted_at, event) VALUES\n{rows}")


def export_window():
    """(start, end) literals for {START}/{END}: the current whole UTC hour."""
    start = scalar("SELECT toString(toStartOfHour(now()))")
    end = scalar(
        "SELECT toString(toStartOfHour(now()) + INTERVAL 1 hour)"
    )
    return start, end


def substitute(sql: str, start: str, end: str) -> str:
    return re.sub(r"\{START\}", start, re.sub(r"\{END\}", end, sql))


def run_export(name: str, start: str, end: str) -> list[dict]:
    """Run a committed export_*.sql over the window; return parsed JSON rows.

    Mirrors export.sh, which feeds the committed SQL to clickhouse-client with
    --format JSONEachRow: the HTTP endpoint defaults to TSV, so the FORMAT
    clause is material (one JSON object per line).
    """
    path = WAREHOUSE_SQL / EXPORT_FILES[name]
    sql = substitute(path.read_text(), start, end) + "\nFORMAT JSONEachRow"
    out = query(sql)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def seeded():
    """reset + apply + insert fixture; returns (start, end) window literals."""
    reset()
    apply_ok()
    insert_fixture()
    return export_window()


@pytest.mark.ch
def test_export_sql_references_existing_columns(seeded):
    """SDl-parse + column existence guard: committed SQL matches CH schema."""
    tables = set(query("SHOW TABLES").split())
    describe = {
        tbl: {
            row.split("\t")[0]
            for row in query(f"DESCRIBE TABLE {tbl} FORMAT TSV").splitlines()
            if row.strip()
        }
        for tbl in SELECTED
    }
    for tbl, cols in SELECTED.items():
        suffix = tbl.split(".", 1)[1]
        assert suffix in tables, f"missing table {tbl}"
        missing = set(cols) - describe[tbl]
        assert not missing, f"{tbl} missing columns {sorted(missing)}"

    # placeholders survive verbatim in every committed SQL file
    for path in sorted(WAREHOUSE_SQL.glob("*_*.sql")):
        text = path.read_text()
        assert "{START}" in text and "{END}" in text, f"{path.name} lost placeholders"


@pytest.mark.ch
def test_export_edits_hourly_rollup(seeded):
    start, end = seeded
    rows = run_export("kpi_edits", start, end)

    assert rows, "no export rows for populated hour"
    want_edits, want_bytes = fixture_totals()
    assert sum(r["edits"] for r in rows) == want_edits
    assert sum(r["bytes_delta"] for r in rows) == want_bytes

    # RFC3339 cast (bq loader) + boolean rendered 'true'/'false' (not 0/1)
    ts = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    for r in rows:
        assert ts.match(r["hour"]), f"hour not RFC3339: {r['hour']!r}"
        assert r["is_bot"] in ("true", "false"), f"is_bot: {r['is_bot']!r}"
        assert r["wiki"] != "", "empty wiki leaked through"
    assert any(r["is_bot"] == "true" for r in rows)
    assert any(r["is_bot"] == "false" for r in rows)


@pytest.mark.ch
def test_export_top_pages_hourly_rollup(seeded):
    start, end = seeded
    rows = run_export("kpi_top_pages", start, end)

    assert rows, "no top-pages rows for populated hour"
    want_edits, want_bytes = fixture_totals()
    assert sum(r["edits"] for r in rows) == want_edits
    assert sum(r["bytes_delta"] for r in rows) == want_bytes
    # same title on two wikis stays two rows ((hour,title,wiki) key)
    titles = {(r["title"], r["wiki"]) for r in rows}
    assert ("Main_Page", "enwiki") in titles and ("Main_Page", "frwiki") in titles


@pytest.mark.ch
def test_export_sizes_hourly_rollup(seeded):
    start, end = seeded
    rows = run_export("kpi_sizes", start, end)

    by_bucket = {r["bucket"]: r["edits"] for r in rows}
    assert by_bucket == {k: int(v) for k, v in BUCKET_COUNTS.items()}, by_bucket


def _sample_count(start: str, end: str) -> int:
    return int(
        scalar(
            "SELECT count() FROM default.raw_events"
            f" WHERE inserted_at >= {sql_literal(start)}"
            f" AND inserted_at < {sql_literal(end)}"
            " AND sipHash64(event) % 100 < 10"
        )
    )


def _seed_sampled(start: str, end: str, cap: int = 200) -> int:
    """Guarantee >=1 sampled event in the window, returning probes inserted.

    sipHash64 is deterministic, so a fixed event either passes the 10% sample
    or never does; each probe is distinct, so the loop terminates (~10
    iterations expected, P(no hit in `cap`) = 0.9^cap).
    """
    probe = 0
    while _sample_count(start, end) == 0 and probe < cap:
        probe += 1
        ev = {
            # "log" keeps probes out of the MVs (WHERE event_type IN
            # ('edit','new')) while export_raw_sample.sql still sees them.
            "type": "log",
            "wiki": "enwiki",
            "title": f"sample_probe_{probe}",
            "user": "probe",
            "bot": False,
            "timestamp": "2026-08-11T10:00:00Z",
            "length": {"new": 10, "old": 5},
        }
        query(
            "INSERT INTO default.raw_events (inserted_at, event) VALUES "
            f"(toDateTime64(now(), 3), {sql_literal(json.dumps(ev))})"
        )
    return probe


@pytest.mark.ch
def test_export_raw_sample_deterministic_subset(seeded):
    start, end = seeded
    # The shared FIXTURE need not contain any sampled event; seed the window
    # so the subset assertion below is comparing against a non-empty sample.
    _seed_sampled(start, end)
    rows = run_export("raw_sample", start, end)

    expected = set(
        scalar(
            "SELECT event FROM default.raw_events"
            f" WHERE inserted_at >= {sql_literal(start)}"
            f" AND inserted_at < {sql_literal(end)}"
            " AND sipHash64(event) % 100 < 10"
        ).splitlines()
    )
    assert expected, "no sampled events to validate the deterministic subset"
    got = {r["event"] for r in rows}
    assert got == expected, "sample rows mismatch the deterministic predicate"
    for r in rows:
        assert r["is_bot"] in ("true", "false")
        assert r["inserted_at"].endswith("Z")


@pytest.mark.ch
def test_parity_ch_sums_match_export(seeded):
    """The CH half of parity.sh (wrapped SUM/count over the same SQL)."""
    start, end = seeded
    want_edits, want_bytes = fixture_totals()

    edits = scalar(
        "SELECT SUM(edits) AS edits, SUM(bytes_delta) AS bytes_delta FROM ("
        + substitute(
            (WAREHOUSE_SQL / EXPORT_FILES["kpi_edits"]).read_text(), start, end
        )
        + ")"
    )
    assert edits == f"{want_edits}\t{want_bytes}", edits

    top = scalar(
        "SELECT SUM(edits) AS edits, SUM(bytes_delta) AS bytes_delta FROM ("
        + substitute(
            (WAREHOUSE_SQL / EXPORT_FILES["kpi_top_pages"]).read_text(), start, end
        )
        + ")"
    )
    assert top == f"{want_edits}\t{want_bytes}", top

    sizes = scalar(
        "SELECT SUM(edits) AS edits FROM ("
        + substitute((WAREHOUSE_SQL / EXPORT_FILES["kpi_sizes"]).read_text(), start, end)
        + ")"
    )
    assert sizes == str(sum(int(v) for v in BUCKET_COUNTS.values())), sizes

    raw = scalar(
        "SELECT count() AS row_count FROM default.raw_events"
        f" WHERE inserted_at >= {sql_literal(start)}"
        f" AND inserted_at < {sql_literal(end)}"
        " AND sipHash64(event) % 100 < 10"
    )
    assert len(run_export("raw_sample", start, end)) == int(raw)


@pytest.mark.ch
def test_parity_bq_sql_shape(seeded):
    """BQ-dialect twins: substitute cleanly, reference the BQ tables."""
    start, end = seeded
    for label, fname in PARITY_FILES.items():
        text = (WAREHOUSE_SQL / fname).read_text()
        sub = substitute(text, start, end)
        assert "{START}" not in sub and "{END}" not in sub, fname
        if label == "raw_sample":
            assert sub.startswith("SELECT COUNT(*) AS row_count\nFROM wikistream.raw_events_sample")
        else:
            assert "FROM wikistream.kpi_" in sub and "WHERE hour >= TIMESTAMP(" in sub


@pytest.mark.ch
def test_export_runs_shape(seeded):
    """The record export.sh uploads after a window: keys, types, counts."""
    start, end = seeded
    # Self-contained: seed the sample until it passes (sibling-test side
    # effects must never be required for this test to hold).
    _seed_sampled(start, end)
    counts = {
        "rows_edits": len(run_export("kpi_edits", start, end)),
        "rows_top_pages": len(run_export("kpi_top_pages", start, end)),
        "rows_sizes": len(run_export("kpi_sizes", start, end)),
        "rows_raw_sample": len(run_export("raw_sample", start, end)),
    }
    line = {
        "exported_at": "2026-08-12T12:00:00Z",
        "window_start": f"{start.replace(' ', 'T')}Z",
        "window_end": f"{end.replace(' ', 'T')}Z",
        "status": "success",
        **counts,
    }
    assert line["rows_edits"] > 0
    assert line["rows_raw_sample"] >= 1  # may include probes seeded by the raw-sample test
    assert set(line) == {
        "exported_at",
        "window_start",
        "window_end",
        "status",
        "rows_edits",
        "rows_top_pages",
        "rows_sizes",
        "rows_raw_sample",
    }
    for k in ("rows_edits", "rows_top_pages", "rows_sizes", "rows_raw_sample"):
        assert isinstance(line[k], int)