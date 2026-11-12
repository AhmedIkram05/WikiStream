"""L7: image builds must not run as the broad deploy identity."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_bootstrap_creates_a_repo_scoped_build_identity():
    bootstrap = (REPO / "infra" / "bootstrap" / "main.tf").read_text()
    assert 'account_id   = "wikistream-build"' in bootstrap
    assert 'role       = "roles/artifactregistry.writer"' in bootstrap
    assert "google_artifact_registry_repository_iam_member" in bootstrap
    assert "google_service_account_iam_member" in bootstrap


def test_build_push_uses_the_build_identity():
    workflow = (REPO / ".github" / "workflows" / "apply.yml").read_text()
    build_job = workflow.split("build-push:", 1)[1].split("apply:", 1)[0]
    assert "wikistream-build@wikistream-505003" in build_job
    assert "wikistream-deploy@wikistream-505003" not in build_job
