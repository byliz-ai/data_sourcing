#!/usr/bin/env bash
#
# smoke_test.sh — end-to-end "clean user" sanity check (NO credentials).
#
# Reproduces the README "First success" path a brand-new user hits: it lists
# the catalog offline, then does two real, no-account fetches — Copernicus DEM
# elevation (ELEV) and SoilGrids clay (CLAY) — clipped to a county and cached,
# and finally confirms the cache filled up. If this passes, install + network +
# clipping + cache all work for someone with zero credentials.
#
# This is an OPTIONAL, NETWORKED check — it is NOT part of `pytest` / CI (the
# test suite stays network-free, see CONTRIBUTING.md). Run it by hand after an
# install or a change to the download/cache path. Takes ~2-4 min the first time.
#
# Prerequisites:
#   - the conda env is active     (conda activate agwise_data)
#   - installed with geo extras   (pip install -e ".[all]"  — or at least ".[geo]")
#   - network access
#
# It writes ONLY to a throwaway cache dir (mktemp) that it deletes on exit, and
# never reads the shared Landing tree — so it is safe to run anywhere.
#
# Usage:   bash scripts/smoke_test.sh
# Env:     PYTHON=<interpreter>   override the python used (default: python/python3)
#          KEEP_CACHE=1           keep the throwaway cache dir for inspection

set -euo pipefail

# --- resolve the interpreter and the CLI runner -----------------------------
PY="${PYTHON:-$(command -v python || command -v python3 || true)}"
if [[ -z "$PY" ]]; then
  echo "FAIL: no python interpreter found (activate the env, or set PYTHON=)." >&2
  exit 1
fi
if command -v agwise-data >/dev/null 2>&1; then
  CLI=(agwise-data)                       # console entry point (README uses this)
else
  CLI=("$PY" -m agwise_data.cli)          # fallback: run the module directly
fi

# --- an isolated, throwaway cache; never touch the shared roots -------------
CACHE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agwise_smoke.XXXXXX")"
cleanup() {
  local code=$?
  if [[ "${KEEP_CACHE:-0}" == "1" ]]; then
    echo "(kept cache dir: $CACHE_DIR)"
  else
    rm -rf "$CACHE_DIR"
  fi
  return $code
}
trap cleanup EXIT

export AGWISE_DATA_ROOT="$CACHE_DIR"      # download cache = the throwaway dir
unset AGWISE_LOCAL_ROOT || true           # force a real download (no Landing shortcut)
export HDF5_USE_FILE_LOCKING=FALSE        # harmless; needed if TMPDIR is on NFS

# --- helpers ----------------------------------------------------------------
STEP=0
step() { STEP=$((STEP + 1)); echo; echo ">>> [$STEP] $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# Read one field out of a JSON blob on stdin via a python expression on `d`
# (the parsed dict). Prints the value; exits non-zero if the expression is
# falsy or raises.
json_get() {
  "$PY" -c '
import sys, json
d = json.load(sys.stdin)
v = eval(sys.argv[1])
if not v and v != 0:
    sys.exit(1)
print(v)
' "$1"
}

REGION=(--country Kenya --admin-level 1 --admin-name Nakuru)

echo "agwise-data smoke test"
echo "  CLI:   ${CLI[*]}"
echo "  cache: $CACHE_DIR  (throwaway)"
echo "  region:${REGION[*]}"

# --- 1. offline: the catalog lists the sources ------------------------------
step "catalog list (offline)"
OUT="$("${CLI[@]}" catalog list)" || fail "catalog list exited non-zero"
echo "$OUT"
echo "$OUT" | json_get "d['ok'] and 'cop_dem30' in d['sources'] and 'soilgrids' in d['sources']" \
  >/dev/null || fail "catalog missing cop_dem30/soilgrids or ok!=true"

# --- 2. a real DEM fetch, no account (Copernicus DEM elevation) -------------
step "get-static ELEV — Copernicus DEM, no credentials (~1-2 min)"
OUT="$("${CLI[@]}" get-static --vars ELEV "${REGION[@]}")" || fail "get-static ELEV failed"
echo "$OUT"
NC="$(echo "$OUT" | json_get "d['ok'] and d['outputs'][0]['nc']")" || fail "no ELEV nc path returned"
[[ -f "$NC" ]] || fail "ELEV NetCDF not on disk: $NC"
echo "    ok: $NC"

# --- 3. a real soil fetch, no account (SoilGrids clay) ----------------------
step "get-static CLAY — SoilGrids, no credentials (~1-2 min)"
OUT="$("${CLI[@]}" get-static --vars CLAY "${REGION[@]}")" || fail "get-static CLAY failed"
echo "$OUT"
NC="$(echo "$OUT" | json_get "d['ok'] and d['outputs'][0]['nc']")" || fail "no CLAY nc path returned"
[[ -f "$NC" ]] || fail "CLAY NetCDF not on disk: $NC"
echo "    ok: $NC"

# --- 4. the cache actually filled up ----------------------------------------
step "cache info — confirm the downloads were cached"
OUT="$("${CLI[@]}" cache info)" || fail "cache info failed"
echo "$OUT"
echo "$OUT" | json_get "d['ok'] and d['n_files'] > 0" >/dev/null \
  || fail "cache is empty after two fetches"

echo
echo "PASS — install, network, clipping and cache all work with no credentials."
