"""Migration suite for migrations/apply.sh (plan §3.1.7).

Runs against a LIVE ClickHouse at localhost:8123 (the CI analytics-tests
matrix entry; compose-smoke applies the same migrations). The container is
disposable — tests may DROP and CREATE tables on every run, so each test
starts from a clean slate via reset().

Env contract (same env vars as apply.sh; CI sets exactly these three):
    CH_HOST     (default localhost)
    CH_PORT     (default 8123)
    CH_USER     (default wikistream)
    CH_PASSWORD (default wikistream_dev_password)
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]


def ch_env():
    return {
        "CH_HOST": os.environ.get("CH_HOST", "localhost"),
        "CH_PORT": os.environ.get("CH_PORT", "8123"),
        "CH_USER": os.environ.get("CH_USER", "wikistream"),
        "CH_PASSWORD": os.environ.get("CH_PASSWORD", "wikistream_dev_password"),
    }


def run_apply(env=None):
    """Run the migration runner; returns the CompletedProcess (caller asserts rc)."""
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


def reset():
    # Disposable-container hygiene. schema_migrations is dropped too: the
    # runner skips anything already recorded, so a prior test's recorded 001
    # would silently defeat this test's fresh apply (no table created).
    query("DROP TABLE IF EXISTS default.raw_events")
    query("DROP TABLE IF EXISTS default.raw_events_v1")
    query("DROP TABLE IF EXISTS default.schema_migrations")


def migration_files():
    # Exactly the top-level [0-9]*.sql files apply.sh globs
    # (bootstrap-user.dev.sql is never recorded).
    return sorted(REPO_ROOT.glob("migrations/[0-9]*.sql"))


def apply_ok():
    r = run_apply()
    assert r.returncode == 0, f"apply.sh exited {r.returncode}:\n{r.stdout}\n{r.stderr}"
    return r


def sql_literal(s: str) -> str:
    return "'" + s.replace("'", "\\'") + "'"


@pytest.mark.ch
def test_clean_db_apply():
    reset()
    apply_ok()
    files = migration_files()

    # every [0-9]*.sql recorded (guard-0 files are `skipped`, not absent)
    n = scalar("SELECT count() FROM default.schema_migrations")
    assert n == str(len(files)), f"recorded {n}, expected {len(files)} migration files"
    bad = scalar(
        "SELECT count() FROM default.schema_migrations"
        " WHERE status NOT IN ('applied', 'skipped')"
    )
    assert bad == "0", f"{bad} rows with unexpected status"

    # raw_events exists with all 8 typed (materialized) columns
    cols = set(
        query(
            "SELECT name FROM system.columns"
            " WHERE database = 'default' AND table = 'raw_events'"
        ).split()
    )
    for col in (
        "wiki",
        "title",
        "user",
        "event_type",
        "is_bot",
        "length_new",
        "length_old",
        "event_timestamp",
    ):
        assert col in cols, f"missing typed column: {col}"


@pytest.mark.ch
def test_re_run_idempotent():
    reset()
    apply_ok()
    first = scalar("SELECT count() FROM default.schema_migrations")
    apply_ok()
    second = scalar("SELECT count() FROM default.schema_migrations")
    assert second == first, f"schema_migrations grew {first} -> {second} on re-run"


@pytest.mark.ch
def test_ttl_present():
    reset()
    apply_ok()
    ttl = ""
    if scalar("SELECT hasColumnInTable('system', 'tables', 'ttl_expression')") == "1":
        ttl = scalar(
            "SELECT ttl_expression FROM system.tables"
            " WHERE database = 'default' AND name = 'raw_events'"
        )
    if ttl:
        assert "toIntervalDay(30)" in ttl, f"unexpected TTL expression: {ttl}"
    else:
        # CH 26.x dropped TTL metadata from system.tables — SHOW CREATE is
        # the version-proof form; it normalizes to toIntervalDay(30).
        show = scalar("SHOW CREATE TABLE default.raw_events")
        assert "TOINTERVALDAY(30)" in show.upper(), f"TTL missing from DDL: {show}"


@pytest.mark.ch
def test_legacy_migration():
    reset()
    # Phase 1/2 old-shape table (no typed columns) + one realistic event.
    # Created on a clean slate so 000_detect_legacy's guard fires
    # (raw_events exists AND has no `wiki` column).
    query(
        "CREATE TABLE IF NOT EXISTS default.raw_events ("
        "  inserted_at DateTime64(3, 'UTC'), event String)"
        " ENGINE = MergeTree ORDER BY inserted_at"
    )
    event = {
        "$schema": "/mediawiki/recentchange/1.0.0",
        "id": 123456,
        "type": "edit",
        "namespace": 0,
        "title": "Main_Page",
        "comment": "test edit",
        "timestamp": "2026-08-11T12:34:56Z",
        "user": "ExampleUser",
        "bot": True,
        "minor": False,
        "length": {"new": 120, "old": 100},
        "wiki": "enwiki",
        "server_url": "https://en.wikipedia.org/wiki/Main_Page",
        "ignored_field": "extraction must skip this",
    }
    json_event = json.dumps(event)
    query(
        f"INSERT INTO default.raw_events (inserted_at, event)"
        f" VALUES ('2026-08-11 12:34:56', {sql_literal(json_event)})"
    )
    apply_ok()

    # typed raw_events backfilled; materialized columns match the JSON
    row = scalar(
        "SELECT wiki, title, `user`, event_type, is_bot, length_new, length_old"
        " FROM default.raw_events FORMAT TSV"
    )
    assert row.split("\t") == [
        "enwiki",
        "Main_Page",
        "ExampleUser",
        "edit",
        "1",
        "120",
        "100",
    ], row

    # v1 renamed by 000, dropped by 003 (or kept while 003 is held back):
    # assert against what is actually in the migrations dir.
    have_v1 = scalar(
        "SELECT count() FROM system.tables"
        " WHERE database = 'default' AND name = 'raw_events_v1'"
    )
    if (REPO_ROOT / "migrations" / "003_drop_raw_events_v1.sql").exists():
        assert have_v1 == "0", "003 applied but raw_events_v1 still present"
    else:
        assert have_v1 == "1", "003 held back but raw_events_v1 missing"


@pytest.mark.ch
def test_materialized_compute():
    reset()
    apply_ok()
    event = {
        "wiki": "enwiki",
        "title": "Synthetic",
        "type": "edit",
        "bot": True,
        "length": {"new": 500, "old": 450},
        "user": "BotUser",
        "timestamp": "2026-08-11T10:00:00Z",
        "extra_field": "ignored at insert",
    }
    query(
        f"INSERT INTO default.raw_events (inserted_at, event)"
        f" VALUES ('2026-08-11 10:00:00', {sql_literal(json.dumps(event))})"
    )
    # Read the MATERIALIZED columns, NOT the event JSON — proves the
    # expressions ran at insert time (no OPTIMIZE / background pass).
    row = scalar(
        "SELECT is_bot, length_new, length_old FROM default.raw_events FORMAT TSV"
    )
    assert row.split("\t") == ["1", "500", "450"], row


@pytest.mark.ch
def test_bootstrap_user():
    # The CI/local wikistream user (migrations/bootstrap-user.dev.sql) can
    # connect and query — canary for the analytics-tests bootstrap step.
    assert scalar("SELECT 1") == "1"
