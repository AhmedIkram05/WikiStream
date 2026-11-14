"""L3: project-scoped bucket names and image refs must not be opaque constants."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_bucket_names_follow_the_project():
    backups = (
        REPO / "infra" / "main" / "modules" / "backups" / "backups.tf"
    ).read_text()
    bigquery = (
        REPO / "infra" / "main" / "modules" / "bigquery" / "bigquery.tf"
    ).read_text()
    assert 'name                        = "${var.project_id}-backups"' in backups
    assert 'name                        = "${var.project_id}-bq-staging"' in bigquery


def test_consumer_image_has_an_override():
    compose = (REPO / "docker-compose.yml").read_text()
    assert "${CONSUMER_IMAGE:-" in compose
