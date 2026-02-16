#!/bin/bash
# FORJA_TEMPLATE_VERSION=0.1.0
set -euo pipefail

# Forja PostToolUse hook wrapper.
# Receives JSON via stdin: {"tool_name": "...", "tool_input": {"file_path": "..."}}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FORJA_DIR="$REPO_ROOT/.forja"
EDIT_COUNT_FILE="$FORJA_DIR/edit-count"
VALIDATOR="$SCRIPT_DIR/forja_validator.py"

VALID_EXTENSIONS="py ts js jsx tsx css html"

# Ensure .forja/ exists
mkdir -p "$FORJA_DIR"

# Read JSON from stdin
INPUT=$(cat)

# Extract file_path using jq
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# No file_path → exit silently
if [[ -z "$FILE_PATH" ]]; then
    exit 0
fi

# Get extension (lowercase)
EXT="${FILE_PATH##*.}"
EXT=$(echo "$EXT" | tr '[:upper:]' '[:lower:]')

# Check if extension matches
MATCH=false
for valid in $VALID_EXTENSIONS; do
    if [[ "$EXT" == "$valid" ]]; then
        MATCH=true
        break
    fi
done

if [[ "$MATCH" == false ]]; then
    exit 0
fi

# Run validator
python3 "$VALIDATOR" check-file "$FILE_PATH" || true

# Edit counter
COUNT=0
if [[ -f "$EDIT_COUNT_FILE" ]]; then
    COUNT=$(cat "$EDIT_COUNT_FILE")
fi
COUNT=$((COUNT + 1))
echo "$COUNT" > "$EDIT_COUNT_FILE"

if (( COUNT % 30 == 0 )); then
    echo ""
    echo "PROBE: Han pasado 30 edits. Verifica que tus decisiones siguen alineadas con features.json"
    echo ""
fi

exit 0
