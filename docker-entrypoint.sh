#!/bin/sh
# Entrypoint: runs as root so it can fix ownership of bind-mounted volumes
# before stepping down to the unprivileged `appuser`.
#
# Bind-mounted host directories (./data, ./export) may be created by
# whatever user runs `docker compose up` on the host (often root, e.g. via
# Komodo/Portainer periphery agents), regardless of the ownership baked
# into the image at build time. Without this fix, SQLite fails with
# "unable to open database file" whenever the host directory isn't already
# owned by the container's appuser (uid 1000).
set -e

chown -R appuser:appuser /data /export

exec gosu appuser "$@"
