"""L5: runtime processes must not stay root inside the containers."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_consumer_runs_as_uid_10001_from_the_start():
    dockerfile = (REPO / "consumer" / "Dockerfile").read_text()
    compose = (REPO / "docker-compose.yml").read_text()
    assert "--uid 10001" in dockerfile
    assert "\nUSER app\n" in dockerfile
    assert "docker-entrypoint.sh" not in dockerfile
    assert 'user: "10001:10001"' in compose
    assert "consumer-init:" in compose
    assert 'user: "0:0"' in compose
    assert "service_completed_successfully" in compose
    assert "chown -R app:app /state" in compose


def test_gx_runs_as_the_unprivileged_user():
    dockerfile = (REPO / "gx" / "Dockerfile").read_text()
    assert "--uid 10001" in dockerfile
    assert "\nUSER app\n" in dockerfile
