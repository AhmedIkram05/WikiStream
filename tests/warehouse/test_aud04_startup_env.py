"""AUD-04: startup.sh must keep .env at 600 across reboots."""

from pathlib import Path

REPO = Path(__file__).parents[2]


def test_startup_restores_env_mode_after_recursive_chmod():
    text = (REPO / "infra" / "main" / "templates" / "startup.sh").read_text()
    chmod_all = text.index("chmod -R a+rX /opt/wikistream")
    chmod_env = text.index("chmod 600 /opt/wikistream/.env")
    install_env = text.index("install -m 600 /dev/null .env")
    cat_env = text.index("cat > .env")
    assert chmod_all < chmod_env < install_env < cat_env
