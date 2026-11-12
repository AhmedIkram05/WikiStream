"""L7: the broad deploy SA grant must stay an explicit, reviewed exception."""

from pathlib import Path

REPO = Path(__file__).parents[2]
README = REPO / "infra" / "bootstrap" / "README.md"
BOOTSTRAP = REPO / "infra" / "bootstrap" / "main.tf"


def test_deploy_roles_are_documented():
    text = README.read_text()
    for role in (
        "roles/compute.admin",
        "roles/artifactregistry.admin",
        "roles/secretmanager.admin",
        "roles/bigquery.admin",
        "roles/storage.admin",
        "roles/monitoring.editor",
    ):
        assert role in text
    assert "documented exception" in text
    assert "Deploy SA roles" in BOOTSTRAP.read_text()
