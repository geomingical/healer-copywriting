#!/bin/zsh
# Generate the copy for one packet directory ($2) into the output directory ($1).
out="$1/${2:t}.json"
cd "$2" || exit 1
if claude -p "請讀取 task.md，並依照其中指示完成。照片在 photos/ 資料夾，請全部讀完。" \
    --setting-sources "" --strict-mcp-config --model opus --tools Read \
    --output-format json --no-session-persistence > "$out.tmp" 2> "$out.err"; then
  mv "$out.tmp" "$out" && rm -f "$out.err" && echo "done ${2:t}"
else
  echo "FAILED ${2:t} (see $out.err)"
fi
