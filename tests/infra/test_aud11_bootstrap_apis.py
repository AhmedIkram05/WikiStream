"""AUD-11: bootstrap must enable every API infra/main consumes."""

from pathlib import Path

REPO = Path(__file__).parents[2]
BOOTSTRAP = REPO / "infra" / "bootstrap" / "main.tf"

REQUIRED_APIS = (
    "bigquerydatatransfer.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
)


def test_bootstrap_enables_infra_main_apis():
    text = BOOTSTRAP.read_text()
    for api in REQUIRED_APIS:
        assert f'"{api}"' in text, f"bootstrap is missing {api}"
