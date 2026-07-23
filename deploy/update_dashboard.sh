#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${TRADIFI_PROJECT_DIR:-/home/wangfeng/workspace/refresh_tradfi}"
PYTHON_BIN="${PYTHON_BIN:-/home/wangfeng/.local/share/uv/python/cpython-3.11-linux-x86_64-gnu/bin/python3.11}"
LOCK_FILE="${TRADIFI_LOCK_FILE:-/tmp/tradifi-dashboard-update.lock}"
DASHBOARD_FILE="${PROJECT_DIR}/data/dashboard.json"

log() {
  printf '[%s] %s\n' "$(date --utc '+%Y-%m-%dT%H:%M:%SZ')" "$*"
}

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "another dashboard update is already running"
  exit 75
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  log "Python runtime is not executable: ${PYTHON_BIN}"
  exit 1
fi

cd "${PROJECT_DIR}"

backup_file="$(mktemp "${TMPDIR:-/tmp}/tradifi-dashboard.XXXXXX.json")"
had_dashboard=0
validated=0

backup_dashboard() {
  if [[ -f "${DASHBOARD_FILE}" ]]; then
    cp -- "${DASHBOARD_FILE}" "${backup_file}"
    had_dashboard=1
  else
    : >"${backup_file}"
    had_dashboard=0
  fi
}

cleanup() {
  local exit_code=$?
  if [[ "${validated}" -eq 0 ]]; then
    if [[ "${had_dashboard}" -eq 1 ]]; then
      cp -- "${backup_file}" "${DASHBOARD_FILE}"
      log "update failed; restored the previous data/dashboard.json"
    else
      rm -f -- "${DASHBOARD_FILE}"
      log "update failed; removed the unvalidated data/dashboard.json"
    fi
  fi
  rm -f -- "${backup_file}"
  exit "${exit_code}"
}
trap cleanup EXIT

backup_dashboard
log "syncing origin/main"
git pull --rebase origin main
backup_dashboard

log "collecting Binance TradFi symbols"
"${PYTHON_BIN}" collectors/binance/fetch_binance_tradifi_symbols.py

log "collecting Binance listing times"
"${PYTHON_BIN}" collectors/binance/fetch_binance_tradifi_listing_times.py

log "collecting Binance 8h funding"
"${PYTHON_BIN}" collectors/binance/fetch_binance_tradifi_funding_8h.py

log "collecting KuCoin 8h funding"
"${PYTHON_BIN}" collectors/kucoin/fetch_kucoin_tradifi_funding_8h.py

log "collecting OKX, Gate, Bitget, Bybit, and Phemex"
"${PYTHON_BIN}" collectors/multi_exchange/fetch_tradifi_data.py \
  --exchange all \
  --history-days 35 \
  --workers 10

log "generating dashboard data and refreshing turnover"
"${PYTHON_BIN}" scripts/generate_dashboard_data.py --refresh-turnover

log "validating generated data"
"${PYTHON_BIN}" deploy/validate_dashboard.py
validated=1

git add -- data/dashboard.json
if git diff --cached --quiet; then
  log "dashboard data is unchanged; no commit created"
else
  git commit -m "data: update TradFi funding dashboard"
fi

log "pushing validated data to origin/main"
git push origin main
log "dashboard update completed successfully"
