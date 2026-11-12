"""Base images must be digest-pinned, not floating tags."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_dockerfiles_pin_base_images_by_digest():
    consumer = (REPO / "consumer" / "Dockerfile").read_text()
    gx = (REPO / "gx" / "Dockerfile").read_text()
    assert "FROM python:3.14-slim@sha256:" in consumer
    assert "FROM python:3.12-slim@sha256:" in gx


def test_compose_pins_service_images_by_digest():
    compose = (REPO / "docker-compose.yml").read_text()
    assert "clickhouse/clickhouse-server:26.3.17@sha256:" in compose
    assert "grafana/grafana:13.1.1@sha256:" in compose
