#!/bin/sh
# Bind-mounted /state is created root-owned by Docker on the VM. Fix ownership
# once, then exec the app as the unprivileged runtime user.
set -eu
if [ "$(id -u)" = "0" ]; then
  state_dir="${STATE_DIR:-/state}"
  mkdir -p "$state_dir"
  chown -R app:app "$state_dir"
  exec python -c 'import os, sys; os.setgid(10001); os.setgroups([]); os.setuid(10001); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
fi
exec "$@"
