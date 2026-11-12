"""L3 follow-up: warehouse bucket defaults must follow the deployed project."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_export_and_backup_buckets_default_to_the_project():
    export = (REPO / "warehouse" / "export.sh").read_text()
    backup = (REPO / "warehouse" / "backup.sh").read_text()
    assert "gs://${GCP_PROJECT:-wikistream-505003}-bq-staging" in export
    assert "gs://${GCP_PROJECT:-wikistream-505003}-backups" in backup


def test_backup_env_file_can_be_overridden():
    backup = (REPO / "warehouse" / "backup.sh").read_text()
    assert "WIKISTREAM_ENV_FILE:-/opt/wikistream/.env" in backup
