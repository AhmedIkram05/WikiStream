"""AUD-31: the post-apply VM reset must follow TF_VAR_ZONE."""

from pathlib import Path

REPO = Path(__file__).parents[2]
APPLY = REPO / ".github" / "workflows" / "apply.yml"


def test_reset_uses_the_configured_zone():
    text = APPLY.read_text()
    assert "--zone us-east1-b" not in text
    assert '--zone "$TF_VAR_ZONE"' in text
