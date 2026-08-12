#!/bin/sh
# Bound the log directory (2026-08-06).
#
#   scripts/rotate_logs.sh [log_dir]        (default: logs/)
#
# WHY THIS IS A SCRIPT AND NOT RotatingFileHandler
# ------------------------------------------------
# The obvious fix — swap `logging.FileHandler` for `RotatingFileHandler` at the
# five handler sites — is actively WRONG here, for two independent reasons.
#
# 1. FOUR of those handlers write the SAME file, `logs/agent.log`, from FOUR
#    SEPARATE PROCESSES: main.py:79 (the cycle), watchdog.py:64,
#    daily_posts.py:48 and backfill_posts.py:170. `flatten_retry` runs every 15
#    minutes around the clock, so overlap is routine, not theoretical. Python's
#    rotation RENAMES the file and opens a new one; the other three processes
#    keep holding O_APPEND descriptors on the renamed inode and go on writing
#    into a file nobody will ever read again. Rotation would silently start
#    LOSING logs — the opposite of the goal.
#
# 2. It cannot reach the biggest file anyway. `logs/cron.log` is written by 13
#    shell `>>` redirects across 12 wrapper scripts, and `logs/launchd.err.log`
#    by `StandardErrorPath` in all 11 plists. No Python handler touches either.
#
# So: one scheduled job, and COPYTRUNCATE rather than rename.
#
#   cp f f.1   &&   : > f
#
# The inode survives. Every process holding an append descriptor keeps writing
# to the same file, which is now empty, instead of to an orphan. That single
# choice is why this needs no change to any Python handler, and why the two
# shell-written files are covered for free.
#
# THE COST, stated rather than hidden: writes landing between the `cp` and the
# truncate are lost. That is exactly logrotate's own `copytruncate` tradeoff.
# Losing a handful of lines beats losing every line four processes write for
# the rest of the day.
#
# NOT URGENT, and the honest reason to keep it small: measured growth is about
# 17 KB per trading day, so 10 MB is ~2.3 years away. What this actually
# defends against is a runaway — `agent.jsonl` grew 144 KB in a single day
# during a backfill, 9x the median day. A crash loop is the case that matters.
set -eu
cd "${AGENT_ROOT:-$(dirname "$0")/..}"

LOGDIR="${1:-logs}"
MAX_BYTES="${REPETE_LOG_MAX_BYTES:-5242880}"     # 5 MB
KEEP="${REPETE_LOG_KEEP:-5}"                     # generations; ~25 MB ceiling/file

[ -d "$LOGDIR" ] || { echo "no log dir at $LOGDIR — nothing to do"; exit 0; }

rotated=0
# The globs are what stop a rotation being rotated: `agent.log.1` ends in `.1`
# and matches neither `*.log` nor `*.jsonl`. An explicit `case` guard was
# written here first and a mutation proved it DEAD — deleting it changed
# nothing, because the pattern had already excluded those files. It is gone
# rather than kept "for safety": a guard that cannot fire is indistinguishable
# from a guard that does not work, and this repo has been bitten by exactly
# that before (see the CI comment about check_secret_exposure.py).
# `tests/test_log_rotation.py::test_a_rotated_file_is_never_itself_rotated`
# pins the property; widening these globs is what would break it.
for f in "$LOGDIR"/*.log "$LOGDIR"/*.jsonl; do
  [ -e "$f" ] || continue                        # unmatched glob

  # `wc -c`, not `stat`: the flags differ between macOS (-f%z) and Linux
  # (-c%s), and CI is Ubuntu while production is a Mac.
  size=$(wc -c < "$f" | tr -d ' ')
  [ "$size" -gt "$MAX_BYTES" ] || continue

  # Age the existing generations off the end first. These are closed files —
  # nothing holds a descriptor on them — so a plain mv is correct and cheap.
  # NOTE ON `if` RATHER THAN `[ x ] && y`: under `set -e` an AND-list whose
  # test fails leaves a non-zero status, and shells disagree about whether that
  # is fatal. A rotation job that exits 1 because a generation happened not to
  # exist would read as a failed job — and once run_rotate_logs.sh alerts on
  # failure, that is a false page. Explicit `if` has no such ambiguity.
  i="$KEEP"
  while [ "$i" -gt 1 ]; do
    prev=$((i - 1))
    if [ -e "$f.$prev" ]; then
      mv "$f.$prev" "$f.$i"
    fi
    i="$prev"
  done

  # COPY, then TRUNCATE IN PLACE. Order matters and so does the truncation
  # method: `: > "$f"` truncates the existing inode. `rm` + recreate would
  # allocate a new one and reintroduce exactly the orphaned-descriptor bug
  # this script exists to avoid.
  cp "$f" "$f.1"
  : > "$f"
  echo "rotated $f ($size bytes) -> $f.1"
  rotated=$((rotated + 1))

  # Drop anything past the keep count (e.g. after KEEP was lowered).
  j=$((KEEP + 1))
  while [ "$j" -le 20 ]; do
    rm -f "$f.$j"
    j=$((j + 1))
  done
done

if [ "$rotated" -eq 0 ]; then
  echo "nothing over ${MAX_BYTES} bytes in $LOGDIR"
fi
exit 0
