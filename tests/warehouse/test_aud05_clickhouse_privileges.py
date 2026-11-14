"""AUD-05: ClickHouse write grants are scoped and Grafana is read-only."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_clickhouse_grants_are_scoped_and_grafana_is_read_only():
    boot = (REPO / "scripts" / "boot.sh").read_text()
    dev = (REPO / "migrations" / "bootstrap-user.dev.sql").read_text()
    for text in (boot, dev):
        assert (
            "GRANT SELECT, INSERT, CREATE, ALTER, DROP, TRUNCATE, OPTIMIZE, BACKUP "
            "ON default.* TO wikistream" in text
        )
        assert "GRANT SELECT ON default.* TO wikistream_ro" in text
        assert "REVOKE ALL PRIVILEGES ON *.* FROM wikistream" in text
        assert "ON *.* TO wikistream" not in text
        assert "ON *.* TO wikistream_ro" not in text

    compose = (REPO / "docker-compose.yml").read_text()
    grafana = compose.split("grafana:", 1)[1].split("data_quality:", 1)[0]
    assert "CLICKHOUSE_RO_PASSWORD" in grafana
    assert "CLICKHOUSE_PASSWORD:" not in grafana

    datasource = (
        REPO / "grafana" / "provisioning" / "datasources" / "clickhouse.yaml"
    ).read_text()
    assert "username: wikistream_ro" in datasource
    assert "password: ${CLICKHOUSE_RO_PASSWORD}" in datasource
