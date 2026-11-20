#!/usr/bin/env bash
# Container entrypoint: Sync ID on 127.0.0.1:8001 (never published), TruthLens on 0.0.0.0:$PORT.
# If either process exits, the container exits so the platform restarts it cleanly.
set -euo pipefail
cd "$(dirname "$0")/.."

# Free plan: no persistent disk, so this is container-local and wiped on every restart/redeploy.
DATA_DIR="${DATA_DIR:-/tmp/truthlens-data}"
PORT="${PORT:-10000}"

export TRUTHLENS_PUBLIC="${TRUTHLENS_PUBLIC:-1}"
export TRUTHLENS_DB="${TRUTHLENS_DB:-$DATA_DIR/truthlens/truthlens.db}"
export TRUTHLENS_REGISTRY="${TRUTHLENS_REGISTRY:-$DATA_DIR/truthlens/registry}"
export TRUTHLENS_DATA="${TRUTHLENS_DATA:-$DATA_DIR/truthlens/cases}"
export SYNCID_DB="${SYNCID_DB:-$DATA_DIR/syncid/registry.sqlite3}"
export SYNCID_KEY_PATH="${SYNCID_KEY_PATH:-$DATA_DIR/syncid/signing-key.pem}"
export SYNCID_ISSUER="${SYNCID_ISSUER:-syncid-truthlens-demo}"
export SYNCID_URL="http://127.0.0.1:8001"
# Shared secret between TruthLens and Sync ID. If the platform did not set one, make a per-boot random one,
# so direct registration on the internal port always needs a token.
if [ -z "${SYNCID_API_TOKEN:-}" ]; then
  SYNCID_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
export SYNCID_API_TOKEN
export HOME="${HOME:-/home/app}"
# Small instance (512 MB, shared CPU): one thread per numeric library, fewer malloc arenas.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}" OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}" \
       MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}" MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"

mkdir -p "$(dirname "$TRUTHLENS_DB")" "$TRUTHLENS_REGISTRY" "$TRUTHLENS_DATA" "$(dirname "$SYNCID_DB")" "$(dirname "$SYNCID_KEY_PATH")"
# Stable signing key from the SYNCID_SIGNING_SEED secret; TruthLens pins its public key.
PINNED="$(python deploy/syncid_key.py)"
if [ -n "$PINNED" ]; then
  export SYNCID_PUBLIC_KEY="$PINNED"
  echo "[start] Sync ID signing key loaded from secret (public key ${PINNED:0:12}...)"
else
  echo "[start] WARNING: SYNCID_SIGNING_SEED not set; Sync ID uses a temporary key that changes on restart" >&2
fi

if [ "$(id -u)" = "0" ] && id app >/dev/null 2>&1; then
  # Hand the data dir to the unprivileged app user, then drop root for both services.
  chown -R app:app "$DATA_DIR" 2>/dev/null || true
  chmod 700 "$(dirname "$SYNCID_KEY_PATH")" "$SYNCID_KEY_PATH" 2>/dev/null || true
  RUN_AS=(setpriv --reuid=app --regid=app --init-groups --)
  # Some hosts drop CAP_SETUID/SETGID; fall back to the current user rather than fail to start.
  if ! "${RUN_AS[@]}" true 2>/dev/null; then
    echo "[start] WARNING: cannot drop privileges here; running as $(id -un)" >&2
    RUN_AS=()
  fi
else
  RUN_AS=()
fi

echo "[start] data dir: $DATA_DIR (public mode: $TRUTHLENS_PUBLIC)"
echo "[start] running as: $(id -un) ${RUN_AS[*]:+(dropping to app)}"
"${RUN_AS[@]}" python -m uvicorn syncid.app:app --host 127.0.0.1 --port 8001 --no-server-header --log-level info --no-access-log 2>&1 | sed -u 's/^/[syncid] /' &
SYNCID_PID=$!

ready=0
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1; then ready=1; echo "[start] Sync ID ready on 127.0.0.1:8001"; break; fi
  if ! kill -0 "$SYNCID_PID" 2>/dev/null; then break; fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  # Keep the public site up: TruthLens reports Sync ID as unavailable instead of the whole service crash-looping.
  echo "[start] WARNING: Sync ID did not start (see [syncid] lines above); continuing with TruthLens only" >&2
  ( while true; do sleep 3600; done ) &
  SYNCID_PID=$!
fi

"${RUN_AS[@]}" python -m uvicorn truthlens.app:app --host 0.0.0.0 --port "$PORT" --no-server-header \
  --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30 &
TRUTHLENS_PID=$!
echo "[start] TruthLens starting on 0.0.0.0:$PORT"

trap 'kill -TERM "$SYNCID_PID" "$TRUTHLENS_PID" 2>/dev/null || true' TERM INT
wait -n "$SYNCID_PID" "$TRUTHLENS_PID"
status=$?
echo "[start] a service exited (status $status); stopping container" >&2
kill -TERM "$SYNCID_PID" "$TRUTHLENS_PID" 2>/dev/null || true
wait || true
exit "$status"
