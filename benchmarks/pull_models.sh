#!/usr/bin/env bash
# Pull the 13-model roster. Tolerates failures: a bad tag is reported at the
# end instead of aborting the loop. Re-run safely — pulls are incremental.
# Total download if starting fresh: roughly 55-65 GB. Ensure disk space.
set -u
cd "$(dirname "$0")"

FAILED=()
while IFS= read -r model; do
  [[ -z "$model" || "$model" == \#* ]] && continue
  echo "=== ollama pull $model ==="
  if ! ollama pull "$model"; then
    FAILED+=("$model")
  fi
done < models_13.txt

echo
echo "=== installed models ==="
ollama list
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo
  echo "FAILED TAGS (fix in models_13.txt before the campaign):"
  printf '  %s\n' "${FAILED[@]}"
  exit 1
fi
echo "All models pulled."
