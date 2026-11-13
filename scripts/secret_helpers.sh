#!/usr/bin/env bash
# Secret handling for the boot path. Sourced by scripts/boot.sh and unit-tested
# directly (tests/scripts/test_secret_helpers.py), so the escaping/redaction
# rules are exercised without running the VM bootstrap.
#
# The current secrets are alphanumeric by policy (infra/main/secrets.tf,
# random_password special=false). These helpers exist so a future policy change
# cannot break the bootstrap SQL or leak a password into the boot/serial log.

# Escape a value for a ClickHouse single-quoted string literal. Backslashes are
# doubled first, then single quotes are backslash-escaped (ClickHouse string
# literals use C-style escapes). The value is passed on stdin, never as a sed
# pattern, so a password's metacharacters cannot alter the substitution.
sql_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e "s/'/\\\\'/g"
}

# Replace every occurrence of each secret (given after the text) with
# <redacted>. Quoted patterns make the substitution literal, so regex/glob
# metacharacters in a password cannot break it or leak through. An empty secret
# is skipped (replacing "" would splice <redacted> between every character).
redact_all() {
  local text=$1
  shift
  local secret
  for secret in "$@"; do
    [ -n "$secret" ] && text=${text//"$secret"/<redacted>}
  done
  printf '%s' "$text"
}
