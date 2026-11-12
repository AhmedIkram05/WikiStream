"""L6: every compose service must cap its json-file log size."""

from pathlib import Path

REPO = Path(__file__).parents[2]
COMPOSE = REPO / "docker-compose.yml"


def test_all_services_share_bounded_logging():
    text = COMPOSE.read_text()
    assert "x-logging: &default-logging" in text
    assert 'max-size: "10m"' in text
    assert 'max-file: "3"' in text
    assert text.count("logging: *default-logging") == 4
