#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

HOURS="${1:-12}"
SLOT="${2:-}"

if [[ -z "$SLOT" ]]; then
  HOUR_NOW="$(date '+%H')"
  if (( 10#$HOUR_NOW < 12 )); then
    SLOT="morning"
  else
    SLOT="evening"
  fi
fi

if [[ "$SLOT" != "morning" && "$SLOT" != "evening" ]]; then
  echo "Invalid slot: $SLOT" >&2
  echo "Usage: $0 [hours] [morning|evening]" >&2
  exit 2
fi

ICLOUD_DIR="${HORIZON_ICLOUD_DIR:-/Users/chenxin/Library/Mobile Documents/com~apple~CloudDocs/1、iCloud work/AI（iCloud）/ai-news-radar}"
POSTS_DIR="$ICLOUD_DIR/posts"

mkdir -p "$ICLOUD_DIR" "$POSTS_DIR" "$PROJECT_DIR/logs"

echo "Running Horizon and exporting to iCloud"
echo "Project path: $PROJECT_DIR"
echo "Hours: $HOURS"
echo "Slot: $SLOT"
echo "iCloud dir: $ICLOUD_DIR"

./run-local.sh "$HOURS"

latest_file() {
  local pattern="$1"
  local file
  file="$(ls -t $pattern 2>/dev/null | head -n 1 || true)"
  if [[ -z "$file" ]]; then
    echo "No generated file found for pattern: $pattern" >&2
    exit 1
  fi
  printf '%s\n' "$file"
}

ZH_SUMMARY="$(latest_file "data/summaries/*-zh.md")"
EN_SUMMARY="$(latest_file "data/summaries/*-en.md")"
ZH_POST="$(latest_file "docs/_posts/*-summary-zh.md")"
EN_POST="$(latest_file "docs/_posts/*-summary-en.md")"

REPORT_DATE="$(date '+%F')"
ZH_TARGET="$ICLOUD_DIR/$REPORT_DATE-$SLOT-zh.md"
EN_TARGET="$ICLOUD_DIR/$REPORT_DATE-$SLOT-en.md"

cp "$ZH_SUMMARY" "$ZH_TARGET"
cp "$EN_SUMMARY" "$EN_TARGET"
cp "$ZH_POST" "$POSTS_DIR/$(basename "$ZH_POST")"
cp "$EN_POST" "$POSTS_DIR/$(basename "$EN_POST")"

LATEST_INDEX="$ICLOUD_DIR/latest.md"
GENERATED_AT="$(date '+%Y-%m-%d %H:%M:%S %Z')"
ZH_BASENAME="$(basename "$ZH_TARGET")"
EN_BASENAME="$(basename "$EN_TARGET")"

{
  echo "# Horizon Daily Latest"
  echo
  echo "- Last generated at: $GENERATED_AT"
  echo "- Slot: $SLOT"
  echo "- Hours: $HOURS"
  echo "- Chinese: ./$ZH_BASENAME"
  echo "- English: ./$EN_BASENAME"
  echo
  echo "## Recent Files"
  if ls "$ICLOUD_DIR"/*-zh.md "$ICLOUD_DIR"/*-en.md >/dev/null 2>&1; then
    ls -t "$ICLOUD_DIR"/*-zh.md "$ICLOUD_DIR"/*-en.md 2>/dev/null \
      | head -n 10 \
      | while IFS= read -r recent_file; do
          echo "- ./$(basename "$recent_file")"
        done
  else
    echo "- No exported reports yet"
  fi
} > "$LATEST_INDEX"

echo "Copied Chinese file: $ZH_TARGET"
echo "Copied English file: $EN_TARGET"
echo "Copied posts dir: $POSTS_DIR"
echo "Latest index path: $LATEST_INDEX"
echo "iCloud export completed"
