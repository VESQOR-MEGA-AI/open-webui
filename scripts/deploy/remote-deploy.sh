#!/usr/bin/env bash
# Runs ON THE SERVER (piped over SSH by .github/workflows/deploy.yaml).
#
# Usage: remote-deploy.sh <compose_dir> <service> <health_url> <keep_backups> <expected_image>
#
# 1. Pulls the new image for <service> (while the old container keeps serving).
# 2. Stops the container and archives its /app/backend/data mount (volume or
#    bind mount) to <compose_dir>/backups/, keeping the newest <keep_backups>.
# 3. Recreates the container and waits for <health_url> to return 200.
set -euo pipefail

COMPOSE_DIR="${1:?compose dir required}"
SERVICE="${2:-open-webui}"
HEALTH_URL="${3:-http://localhost:3000/health}"
KEEP_BACKUPS="${4:-5}"
EXPECTED_IMAGE="${5:-}"
DATA_DIR=/app/backend/data

log() { printf '\n==> %s\n' "$*"; }

cd "$COMPOSE_DIR"

if docker compose version >/dev/null 2>&1; then
	compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
	compose() { docker-compose "$@"; }
else
	echo "docker compose is not installed on this host" >&2
	exit 1
fi

IMAGE="$(compose config --images "$SERVICE" 2>/dev/null | head -n1 || true)"
log "Service '$SERVICE' uses image: ${IMAGE:-<unknown>}"
if [ -n "$EXPECTED_IMAGE" ] && [ -n "$IMAGE" ] && [[ "${IMAGE,,}" != "${EXPECTED_IMAGE,,}"* ]]; then
	echo "::warning::'$SERVICE' uses '$IMAGE', not '$EXPECTED_IMAGE'. The image built from this repo will NOT be deployed; update the compose file."
fi

log "Pulling new image"
compose pull "$SERVICE"

CID="$(compose ps -q "$SERVICE" || true)"
if [ -n "$CID" ]; then
	log "Stopping $SERVICE for a consistent backup"
	# If anything below fails before the new container starts, bring the old one back.
	trap 'echo "Deploy failed; restarting previous container" >&2; compose start "$SERVICE" || true' ERR
	compose stop "$SERVICE"

	mkdir -p backups
	BACKUP="backups/open-webui-data-$(date -u +%Y%m%dT%H%M%SZ).tgz"
	log "Backing up $DATA_DIR to $COMPOSE_DIR/$BACKUP"
	docker run --rm --volumes-from "$CID" alpine:3 tar czf - -C "$DATA_DIR" . >"$BACKUP"
	ls -lh "$BACKUP"

	# Keep only the newest $KEEP_BACKUPS archives.
	ls -1t backups/open-webui-data-*.tgz | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm -f
else
	log "No existing container for $SERVICE; skipping backup (first deploy?)"
fi

log "Starting $SERVICE"
trap - ERR
compose up -d "$SERVICE"

log "Waiting for $HEALTH_URL (migrations can take a few minutes)"
for i in $(seq 1 60); do
	if curl -fsS -o /dev/null "$HEALTH_URL"; then
		VERSION_URL="${HEALTH_URL%/health}/api/version"
		log "Healthy after ~$((i * 5))s. Version: $(curl -fsS "$VERSION_URL" 2>/dev/null || echo unknown)"
		docker image prune -f >/dev/null 2>&1 || true
		exit 0
	fi
	sleep 5
done

echo "::error::$SERVICE did not become healthy within 5 minutes. Recent logs:" >&2
compose logs --tail=100 "$SERVICE" >&2 || true
[ -n "${BACKUP:-}" ] && echo "Data backup taken before this deploy: $COMPOSE_DIR/$BACKUP" >&2
exit 1
