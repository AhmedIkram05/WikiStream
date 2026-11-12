"""L5: runtime processes must not stay root inside the containers."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_consumer_drops_privileges_after_fixing_state_ownership():
    dockerfile = (REPO / "consumer" / "Dockerfile").read_text()
    entrypoint = (REPO / "consumer" / "docker-entrypoint.sh").read_text()
    assert "--uid 10001" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in dockerfile
    assert 'state_dir="${STATE_DIR:-/state}"' in entrypoint
    assert 'chown -R app:app "$state_dir"' in entrypoint
    assert "setuid(10001)" in entrypoint
    assert "setgid(10001)" in entrypoint


def test_gx_runs_as_the_unprivileged_user():
    dockerfile = (REPO / "gx" / "Dockerfile").read_text()
    assert "--uid 10001" in dockerfile
    assert "\nUSER app\n" in dockerfile
