"""AUD-20: third-party actions and the uv helper image are pinned."""

import re
from pathlib import Path

REPO = Path(__file__).parents[2]
WORKFLOWS = REPO / ".github" / "workflows"

USES = re.compile(r"uses:\s+([^\s#]+)@([^\s#]+)")
SHA = re.compile(r"[0-9a-f]{40}")


def test_every_action_ref_is_a_commit_sha():
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for match in USES.finditer(workflow.read_text()):
            action, ref = match.groups()
            assert SHA.fullmatch(ref), (
                f"{workflow.name}: {action}@{ref} is not a commit SHA"
            )


def test_dockerfiles_pin_uv_by_digest():
    text = (REPO / "consumer" / "Dockerfile").read_text()
    assert "ghcr.io/astral-sh/uv@sha256:" in text
    assert "ghcr.io/astral-sh/uv:latest" not in text
