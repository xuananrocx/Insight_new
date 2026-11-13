#!/bin/sh
# Run the versioned controller with the bundled Python, including on Python-2 hosts.
# Arguments and all paths are explicit; this never installs a host runtime.
set -eu
bundle=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker_bin=${INSIGHT_DOCKER:-docker}
docker_socket=${INSIGHT_DOCKER_SOCKET:-/var/run/docker.sock}
image=$(cat "$bundle/mcp-image.id")
case "$image" in sha256:*) ;; *) exit 1 ;; esac
"$docker_bin" -H "unix://$docker_socket" image inspect "$image" >/dev/null 2>&1 ||
    "$docker_bin" -H "unix://$docker_socket" load -i "$bundle/images.tar"
exec "$docker_bin" -H "unix://$docker_socket" run --rm --pull never \
    --user 0:0 --network host --pid host --read-only --no-healthcheck --tmpfs /tmp:size=32m \
    --security-opt no-new-privileges --mount type=bind,source=/,target=/host \
    --entrypoint python "$image" "/host$bundle/host-runner.py" "$@"
