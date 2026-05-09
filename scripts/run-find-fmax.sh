#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "${NO_COLOR:-}" == "1" ]] || [[ ! -t 1 ]]; then
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GRN=""; C_YLW=""; C_BLU=""; C_MAG=""; C_CYN=""
else
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_RED=$'\033[31m'
  C_GRN=$'\033[32m'
  C_YLW=$'\033[33m'
  C_BLU=$'\033[34m'
  C_MAG=$'\033[35m'
  C_CYN=$'\033[36m'
fi

banner() {
  printf "\n%s%s============================================================%s\n" "$C_BOLD" "$C_CYN" "$C_RESET"
  printf "%s%s%s\n" "$C_BOLD" "$1" "$C_RESET"
  printf "%s%s============================================================%s\n" "$C_BOLD" "$C_CYN" "$C_RESET"
}

clear_line() {
  # Prevent carriage-return spinner artifacts in saved terminal transcripts.
  printf "\r\033[2K"
}

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run-find-fmax.sh [options] [-- <extra find_fmax.py args>]

Options:
  --deck PATH             Base deck (default: spice/top.spi)
  --verify-macros N       Steady verify macros (default: 32)
  --min-period-ns X       Fast bound (default: 0.10)
  --max-period-ns X       Slow bound (default: 2.00)
  --tol-ns X              Search tolerance (default: 0.005)
  --area-wmin X           Bitcell area term for FOM (default: 8)
  --out-json PATH         Save JSON to this path (default: spice/find_fmax_last.json)
  --no-spinner            Disable spinner animation
  -h, --help              Show help

Examples:
  ./scripts/run-find-fmax.sh
  ./scripts/run-find-fmax.sh --verify-macros 8
  ./scripts/run-find-fmax.sh --deck spice/top_opt_best.spi --verify-macros 8
EOF
}

DECK="spice/top.spi"
VERIFY_MACROS="32"
MIN_NS="0.10"
MAX_NS="2.00"
TOL_NS="0.005"
AREA_WMIN="8"
OUT_JSON="spice/find_fmax_last.json"
SPINNER="1"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deck) DECK="$2"; shift 2 ;;
    --verify-macros) VERIFY_MACROS="$2"; shift 2 ;;
    --min-period-ns) MIN_NS="$2"; shift 2 ;;
    --max-period-ns) MAX_NS="$2"; shift 2 ;;
    --tol-ns) TOL_NS="$2"; shift 2 ;;
    --area-wmin) AREA_WMIN="$2"; shift 2 ;;
    --out-json) OUT_JSON="$2"; shift 2 ;;
    --no-spinner) SPINNER="0"; shift 1 ;;
    --) shift; EXTRA_ARGS+=("$@"); break ;;
    -h|--help) usage; exit 0 ;;
    *) EXTRA_ARGS+=("$1"); shift 1 ;;
  esac
done

PY=(python3 spice/find_fmax.py
  --deck "$DECK"
  --verify-macro-cycles "$VERIFY_MACROS"
  --min-period-ns "$MIN_NS"
  --max-period-ns "$MAX_NS"
  --tol-ns "$TOL_NS"
  --bitcell-area-wmin "$AREA_WMIN"
  --save-json "$OUT_JSON"
  --format pretty
)

banner "Full SRAM W/W/R/R Sustained f_max Sweep (Steady-State Verified)"
printf "%s%sDeck:%s %s\n" "$C_BOLD" "$C_MAG" "$C_RESET" "$DECK"
extra_s=""
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  extra_s=" ${EXTRA_ARGS[*]}"
fi
printf "%s%sCommand:%s %s\n\n" "$C_BOLD" "$C_MAG" "$C_RESET" "${PY[*]}${extra_s}"

run_start_epoch="$(date +%s)"
start_ts="$(date +'%Y-%m-%d %H:%M:%S')"
printf "%s%sStart:%s %s\n" "$C_DIM" "$C_CYN" "$C_RESET" "$start_ts"

tmp_log="${TMPDIR:-/tmp}/find_fmax_run_$$.log"
cleanup() { rm -f "$tmp_log"; }
trap cleanup EXIT

if [[ "$SPINNER" == "1" ]]; then
  # Run in background and show a simple spinner.
  set +e
  if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    ( "${PY[@]}" "${EXTRA_ARGS[@]}" ) >"$tmp_log" 2>&1 &
  else
    ( "${PY[@]}" ) >"$tmp_log" 2>&1 &
  fi
  pid=$!
  set -e
  frames=('|' '/' '-' '\')
  i=0
  while kill -0 "$pid" 2>/dev/null; do
    printf "\r%s%sRunning sweep%s %s" "$C_BOLD" "$C_YLW" "$C_RESET" "${frames[$((i % 4))]}"
    i=$((i + 1))
    sleep 0.15
  done
  wait "$pid" || { clear_line; printf "%s%sFAILED%s\n\n" "$C_BOLD" "$C_RED" "$C_RESET"; cat "$tmp_log"; exit 1; }
  clear_line
  printf "%s%sDONE%s\n\n" "$C_BOLD" "$C_GRN" "$C_RESET"
  cat "$tmp_log"
else
  if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    "${PY[@]}" "${EXTRA_ARGS[@]}"
  else
    "${PY[@]}"
  fi
fi

end_ts="$(date +'%Y-%m-%d %H:%M:%S')"
run_end_epoch="$(date +%s)"
elapsed_s="$((run_end_epoch - run_start_epoch))"
elapsed_fmt="$(printf '%02d:%02d' $((elapsed_s / 60)) $((elapsed_s % 60)))"
printf "\n%s%sEnd:%s   %s\n" "$C_DIM" "$C_CYN" "$C_RESET" "$end_ts"
printf "%s%sElapsed:%s %s (mm:ss)\n" "$C_DIM" "$C_CYN" "$C_RESET" "$elapsed_fmt"
printf "%s%sSaved JSON:%s %s\n" "$C_DIM" "$C_CYN" "$C_RESET" "$OUT_JSON"

