"""L3 follow-up: runtime project wiring must follow the deployed project."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_startup_env_carries_project_and_gx_image():
    startup = (REPO / "infra" / "main" / "templates" / "startup.sh").read_text()
    assert "GCP_PROJECT=${GCP_PROJECT}" in startup
    assert (
        "GX_IMAGE=us-central1-docker.pkg.dev/${GCP_PROJECT}/wikistream-consumer/gx:latest"
        in startup
    )
    example = (REPO / ".env.example").read_text()
    assert "GCP_PROJECT=" in example
    assert "GX_IMAGE=" in example


def test_grafana_bigquery_default_project_is_not_hardcoded():
    datasource = (
        REPO / "grafana" / "provisioning" / "datasources" / "bigquery.yaml"
    ).read_text()
    assert "defaultProject: ${GCP_PROJECT}" in datasource
    compose = (REPO / "docker-compose.yml").read_text()
    assert "GCP_PROJECT: ${GCP_PROJECT:-wikistream-505003}" in compose
