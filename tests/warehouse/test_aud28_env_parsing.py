"""AUD-28: export/parity read .env as data instead of sourcing it."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_export_and_backup_do_not_source_env():
    for name in ("export.sh", "backup.sh"):
        text = (REPO / "warehouse" / name).read_text()
        assert ". /opt/wikistream/.env" not in text
        assert "WIKISTREAM_ENV_FILE" in text
        assert "grep -E '^CLICKHOUSE_PASSWORD='" in text


def test_parity_reads_env_as_data():
    text = (REPO / "warehouse" / "parity.py").read_text()
    assert ". /opt/wikistream/.env" not in text
    assert "WIKISTREAM_ENV_FILE" in text
    assert "CLICKHOUSE_PASSWORD" in text
    assert 'split("=", 1)' in text  # value read as data, never sourced
