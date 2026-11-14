"""L3 follow-up: runtime project wiring must follow the deployed project."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_startup_env_carries_project_and_consumer_image():
    startup = (REPO / "infra" / "main" / "templates" / "startup.sh").read_text()
    assert "GCP_PROJECT=${GCP_PROJECT}" in startup
    assert (
        "CONSUMER_IMAGE=us-central1-docker.pkg.dev/${GCP_PROJECT}/wikistream-consumer/consumer:latest"
        in startup
    )
    assert "GX_IMAGE=" not in startup
    example = (REPO / ".env.example").read_text()
    assert "GCP_PROJECT=" in example
    assert "CONSUMER_IMAGE=" in example
    assert "DATA_QUALITY_TABLE=" in example


def test_grafana_bigquery_default_project_is_not_hardcoded():
    datasource = (
        REPO / "grafana" / "provisioning" / "datasources" / "bigquery.yaml"
    ).read_text()
    assert "defaultProject: ${GCP_PROJECT}" in datasource
    compose = (REPO / "docker-compose.yml").read_text()
    assert "GCP_PROJECT: ${GCP_PROJECT:-wikistream-505003}" in compose


def test_data_quality_env_overrides_are_wired():
    compose = (REPO / "docker-compose.yml").read_text()
    for needle in (
        "${DATA_QUALITY_TABLE:-default.raw_events}",
        "${DATA_QUALITY_WINDOW_HOURS:-1}",
        "${DATA_QUALITY_ROW_MIN:-50000}",
        "${DATA_QUALITY_ROW_MAX:-5000000}",
    ):
        assert needle in compose


def test_boot_seeds_data_quality_after_migrations():
    boot = (REPO / "scripts" / "boot.sh").read_text()
    assert "docker compose run --rm -T data_quality || true" in boot
    assert boot.index("migrations/apply.sh") < boot.index(
        "docker compose run --rm -T data_quality"
    )
