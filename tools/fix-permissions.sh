#!/usr/bin/env bash
# Restore executable bits and LF line endings after the kit passed through Windows (copy/zip/git without
# core.autocrlf=input). Run once in the Linux build environment:  bash tools/fix-permissions.sh
# In a git repo, also record the bits:  git add --chmod=+x <files>  (the script prints the command).
set -euo pipefail
cd "$(dirname "$0")/.."
mapfile -t files < <(find . -path ./reference -prune -o \( -name '*.sh' -o -path '*/wrapper/run.sh' -o -path './tools/*.py' -o -path './.claude/hooks/*.py' \) -type f -print)
for f in "${files[@]}"; do
  sed -i 's/\r$//' "$f"
  chmod +x "$f"
done
# LF for all text files we own (not reference/)
find . -path ./reference -prune -o -path ./.git -prune -o -type f \( -name '*.py' -o -name '*.json' -o -name '*.yaml' -o -name '*.md' -o -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.toml' \) -print0 | xargs -0 sed -i 's/\r$//'
echo "Fixed ${#files[@]} executable files and line endings."
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Record exec bits in git:  git add --chmod=+x ${files[*]}"
fi
