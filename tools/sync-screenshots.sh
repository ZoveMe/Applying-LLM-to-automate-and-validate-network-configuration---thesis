#!/usr/bin/env bash
# Copy every uploaded image into docs/screenshots/, skipping ones already there.
# Matching is by SHA-256 of the file contents, so re-running never duplicates.
set -euo pipefail
UP=/sessions/ecstatic-pensive-volta/mnt/uploads
DEST=/sessions/ecstatic-pensive-volta/mnt/thesis-net/docs/screenshots
mkdir -p "$DEST"

# hashes already present
declare -A have
while read -r h f; do have["$h"]=1; done < <(find "$DEST" -name '*.png' -exec sha256sum {} +)

n=$(find "$DEST" -name '[0-9]*_*.png' | wc -l)
added=0
# oldest first so numbering follows upload order
while read -r ts path; do
  h=$(sha256sum "$path" | cut -d' ' -f1)
  [[ -n "${have[$h]:-}" ]] && continue
  n=$((n+1)); added=$((added+1))
  stamp=$(date -d "@${ts%.*}" +%Y-%m-%d_%H-%M)
  cp "$path" "$DEST/$(printf '%02d' $n)_$stamp.png"
  have["$h"]=1
  echo "  + $(printf '%02d' $n)_$stamp.png"
done < <(find "$UP" -name '*.png' -printf '%T@ %p\n' | sort -n)

echo "added: $added | total in folder: $(ls -1 "$DEST"/*.png | wc -l)"
