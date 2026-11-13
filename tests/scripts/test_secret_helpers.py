"""Unit tests for scripts/secret_helpers.sh (boot-path escaping/redaction).

The helpers are sourced by boot.sh, so a password with a quote/backslash/slash
must not break the bootstrap SQL or leak into the log. These run the real
functions in bash with adversarial values.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
HELPERS = REPO / "scripts" / "secret_helpers.sh"


def call(fn: str, *args: str) -> str:
    """Run one helper from secret_helpers.sh and return its stdout."""
    proc = subprocess.run(
        ["bash", "-c", 'source "$1"; "$2" "${@:3}"', "_", str(HELPERS), fn, *args],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("abc", "abc"),
        ("a'b", "a\\'b"),
        ("a\\b", "a\\\\b"),
        ("a'\\b", "a\\'\\\\b"),
        # ClickHouse string literals do not shell-expand: these stay as-is.
        ("a/b&c", "a/b&c"),
        ("a$b`c", "a$b`c"),
        ('a"b', 'a"b'),
        ("a b", "a b"),
        ("", ""),
    ],
)
def test_sql_escape(raw, escaped):
    assert call("sql_escape", raw) == escaped


def test_redact_all_handles_metacharacters_and_repeats():
    assert (
        call("redact_all", "pw=a'b/c ro=x&y", "a'b/c", "x&y")
        == "pw=<redacted> ro=<redacted>"
    )
    assert call("redact_all", "abcabc", "abc") == "<redacted><redacted>"


def test_redact_all_skips_empty_secret():
    # An empty pattern would splice <redacted> between every character.
    assert call("redact_all", "keep", "") == "keep"


def test_boot_uses_helpers_and_not_sed_redaction():
    text = (REPO / "scripts" / "boot.sh").read_text()
    assert "secret_helpers.sh" in text
    assert "sql_escape" in text
    assert "redact_all" in text
    assert 'sed "s/${CH_RO_PASSWORD}' not in text
