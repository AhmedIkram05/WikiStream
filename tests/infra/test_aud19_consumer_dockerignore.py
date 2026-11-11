"""AUD-19: the consumer build context needs its own .dockerignore."""

from pathlib import Path

REPO = Path(__file__).parents[2]
IGNORE = REPO / "consumer" / ".dockerignore"


def test_consumer_context_excludes_local_venv():
    assert IGNORE.is_file(), "consumer/.dockerignore is missing"
    lines = {line.strip() for line in IGNORE.read_text().splitlines()}
    assert ".venv/" in lines
    assert "__pycache__/" in lines
