#!/bin/zsh
# Generate one copy per packet in a fresh, isolated Claude session (no settings, plugins or MCP).
# Usage: scripts/run.sh <packets folder> <outputs folder>
# Existing outputs are kept, so re-running only fills in what is missing.
[[ -d "$1" && -n "$2" ]] || { echo "usage: $0 <packets folder> <outputs folder>"; exit 1; }
command -v claude >/dev/null || { echo "需要先安裝並登入 Claude Code（claude 指令）"; exit 1; }
HERE=${0:A:h}
PACKETS=${1:A}
OUT=${2:A}
mkdir -p "$OUT"
for dir in "$PACKETS"/*(/); do
  [[ -s "$OUT/${dir:t}.json" ]] || print -r -- "$dir"
done | xargs -P 4 -n 1 "$HERE/gen_one.sh" "$OUT"
