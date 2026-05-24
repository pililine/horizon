#!/usr/bin/env bash
set -euo pipefail

RUN_START_EPOCH="$(date +%s)"
RUN_STAMP="$(date '+%Y%m%d-%H%M%S')"
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
RUN_LOG="$PROJECT_DIR/logs/horizon-icloud-$RUN_STAMP.log"
RUN_DATE="$(date '+%F')"
RUN_HHMM="$(date '+%H%M')"
RUN_HHMMSS="$(date '+%H%M%S')"
DAILY_DIR="$ICLOUD_DIR/daily/$RUN_DATE"
POSTS_DIR="$ICLOUD_DIR/posts/$RUN_DATE"
UPDATE_LATEST="${HORIZON_UPDATE_LATEST:-0}"
EXPORT_POSTS="${HORIZON_EXPORT_POSTS:-0}"

mkdir -p "$ICLOUD_DIR" "$DAILY_DIR" "$PROJECT_DIR/logs"
touch "$RUN_LOG"
exec > >(tee -a "$RUN_LOG") 2>&1

load_env_file() {
  local env_file="$1"
  local line key value

  if [[ ! -f "$env_file" ]]; then
    echo ".env not found; relying on inherited environment"
    return 0
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value%$'\r'}"
    if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "$key=$value"
  done < "$env_file"

  echo ".env loaded from project root"
}

require_recent_file() {
  local file="$1"
  local label="$2"
  local mtime

  if [[ ! -f "$file" ]]; then
    echo "Missing $label file: $file" >&2
    exit 1
  fi

  mtime="$(stat -f '%m' "$file")"
  if (( mtime < RUN_START_EPOCH )); then
    echo "Refusing to copy stale $label file: $file" >&2
    echo "File mtime is before this run started." >&2
    exit 1
  fi
}

extract_log_value() {
  local pattern="$1"
  grep -E "$pattern" "$RUN_LOG" | tail -n 1 || true
}

check_append_writable_dir() {
  local dir="$1"
  local label="$2"
  local probe="$dir/.horizon-write-test-$RUN_STAMP-$$"

  if ! printf 'horizon write test\n' > "$probe"; then
    echo "ERROR: Failed to write permission test file in $dir" >&2
    echo "Please check macOS iCloud permissions / Full Disk Access." >&2
    exit 1
  fi
  if ! rm -f "$probe"; then
    echo "WARNING: Failed to remove permission test file in $dir: $probe" >&2
    echo "Append-only export can continue, but you may want to clean it up manually." >&2
  fi
  echo "$label accepts new files"
}

unique_path() {
  local dir="$1"
  local stem="$2"
  local ext="$3"
  local fallback_stem="$4"
  local candidate="$dir/$stem$ext"
  local index=1

  if [[ ! -e "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  candidate="$dir/$fallback_stem$ext"
  while [[ -e "$candidate" ]]; do
    candidate="$dir/$fallback_stem-$index$ext"
    index=$((index + 1))
  done

  printf '%s\n' "$candidate"
}

copy_append_only() {
  local source="$1"
  local target="$2"

  if [[ -e "$target" ]]; then
    echo "ERROR: Refusing to overwrite existing iCloud export file: $target" >&2
    exit 1
  fi
  if ! cp "$source" "$target"; then
    echo "ERROR: Failed to copy $source to export file $target" >&2
    echo "Please check macOS iCloud permissions / Full Disk Access." >&2
    exit 1
  fi
}

write_latest_index() {
  local target="$1"
  local tmp="$target.tmp.$$"

  if ! {
    echo "# Horizon Daily Latest"
    echo
    echo "- Last generated at: $GENERATED_AT"
    echo "- Slot: $SLOT"
    echo "- Hours: $HOURS"
    echo "- Chinese: $ZH_RELATIVE"
    echo "- English: $EN_RELATIVE"
    echo "- Source run log: $RUN_LOG"
    echo
    echo "## Recent Files"
    if [[ -d "$ICLOUD_DIR/daily" ]] && find "$ICLOUD_DIR/daily" -type f \( -name '*-zh.md' -o -name '*-en.md' \) -print -quit 2>/dev/null | grep -q .; then
      find "$ICLOUD_DIR/daily" -type f \( -name '*-zh.md' -o -name '*-en.md' \) -print 2>/dev/null \
        | sort -r \
        | head -n 10 \
        | while IFS= read -r recent_file; do
            echo "- ${recent_file#$ICLOUD_DIR/}"
          done
    else
      echo "- No exported reports yet"
    fi
  } > "$tmp"; then
    rm -f "$tmp"
    echo "ERROR: Failed to write latest index to $target" >&2
    echo "Please check macOS iCloud permissions / Full Disk Access." >&2
    exit 1
  fi

  if ! mv -f "$tmp" "$target"; then
    rm -f "$tmp"
    echo "ERROR: Failed to write latest index to $target" >&2
    echo "Please check macOS iCloud permissions / Full Disk Access." >&2
    exit 1
  fi
}

load_env_file "$PROJECT_DIR/.env"

echo "Running Horizon and exporting to iCloud"
echo "Project path: $PROJECT_DIR"
echo "Hours: $HOURS"
echo "Slot: $SLOT"
echo "iCloud dir: $ICLOUD_DIR"
echo "Daily dir: $DAILY_DIR"
echo "Run log: $RUN_LOG"
echo "Update latest.md: $UPDATE_LATEST"
echo "Export posts: $EXPORT_POSTS"

echo "Checking iCloud export directory writability..."
mkdir -p "$ICLOUD_DIR" "$DAILY_DIR"
check_append_writable_dir "$ICLOUD_DIR" "iCloud dir"
check_append_writable_dir "$DAILY_DIR" "Daily export dir"
if [[ "$EXPORT_POSTS" == "1" ]]; then
  mkdir -p "$POSTS_DIR"
  check_append_writable_dir "$POSTS_DIR" "Posts export dir"
fi

if [[ -n "${LOCAL_LLM_API_KEY:-}" ]]; then
  echo "LOCAL_LLM_API_KEY is set"
else
  echo "LOCAL_LLM_API_KEY is not set" >&2
  exit 1
fi

echo "Checking Ollama OpenAI-compatible endpoint..."
MODELS_JSON="$(curl -fsS http://localhost:11434/v1/models)"
if grep -q '"id":"qwen2.5:14b"' <<< "$MODELS_JSON"; then
  echo "Ollama qwen2.5:14b available"
else
  echo "Ollama is reachable, but qwen2.5:14b was not found" >&2
  exit 1
fi

./run-local.sh "$HOURS"

FINAL_OUTPUT_LINE="$(extract_log_value 'Output: [0-9]+ items selected')"
FINAL_OUTPUT_COUNT="$(sed -nE 's/.*Output: ([0-9]+) items selected.*/\1/p' <<< "$FINAL_OUTPUT_LINE")"
FULL_ENRICHMENT_COUNT="$(sed -nE 's/.*full_enrichment_count=([0-9]+).*/\1/p' "$RUN_LOG" | tail -n 1)"
BRIEF_ENRICHMENT_COUNT="$(sed -nE 's/.*brief_enrichment_count=([0-9]+).*/\1/p' "$RUN_LOG" | tail -n 1)"
SKIPPED_ENRICHMENT_COUNT="$(sed -nE 's/.*skipped_enrichment_count=([0-9]+).*/\1/p' "$RUN_LOG" | tail -n 1)"

echo "Run summary:"
echo "  ${FINAL_OUTPUT_LINE:-final output count unavailable}"
echo "  full=${FULL_ENRICHMENT_COUNT:-unknown}, brief=${BRIEF_ENRICHMENT_COUNT:-unknown}, skipped=${SKIPPED_ENRICHMENT_COUNT:-unknown}"

if [[ -n "$FINAL_OUTPUT_COUNT" && "$FINAL_OUTPUT_COUNT" -lt 5 ]]; then
  echo "WARNING: final output count is low ($FINAL_OUTPUT_COUNT)"
fi

ERROR_PATTERN='Missing API key|local LLM unavailable|JSON parse error|timed out|TimeoutError|request timeout|502|proxy error|localhost.*(failed|refused|error)'
if grep -Eiq "$ERROR_PATTERN" "$RUN_LOG"; then
  echo "WARNING: run log contains possible LLM/API errors"
  grep -Ein "$ERROR_PATTERN" "$RUN_LOG" || true
fi

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

require_recent_file "$ZH_SUMMARY" "Chinese summary"
require_recent_file "$EN_SUMMARY" "English summary"

ZH_TARGET="$(unique_path "$DAILY_DIR" "$RUN_DATE-$RUN_HHMM-$SLOT-zh" ".md" "$RUN_DATE-$RUN_HHMMSS-$SLOT-zh")"
EN_TARGET="$(unique_path "$DAILY_DIR" "$RUN_DATE-$RUN_HHMM-$SLOT-en" ".md" "$RUN_DATE-$RUN_HHMMSS-$SLOT-en")"

echo "Copying fresh Markdown files to iCloud..."
copy_append_only "$ZH_SUMMARY" "$ZH_TARGET"
copy_append_only "$EN_SUMMARY" "$EN_TARGET"

COPIED_POSTS="skipped"
if [[ "$EXPORT_POSTS" == "1" ]]; then
  ZH_POST="$(latest_file "docs/_posts/*-summary-zh.md")"
  EN_POST="$(latest_file "docs/_posts/*-summary-en.md")"
  require_recent_file "$ZH_POST" "Chinese post"
  require_recent_file "$EN_POST" "English post"

  ZH_POST_TARGET="$(unique_path "$POSTS_DIR" "${RUN_DATE}-${RUN_HHMM}-summary-zh" ".md" "${RUN_DATE}-${RUN_HHMMSS}-summary-zh")"
  EN_POST_TARGET="$(unique_path "$POSTS_DIR" "${RUN_DATE}-${RUN_HHMM}-summary-en" ".md" "${RUN_DATE}-${RUN_HHMMSS}-summary-en")"
  copy_append_only "$ZH_POST" "$ZH_POST_TARGET"
  copy_append_only "$EN_POST" "$EN_POST_TARGET"
  COPIED_POSTS="$POSTS_DIR"
fi

LATEST_INDEX="$ICLOUD_DIR/latest.md"
GENERATED_AT="$(date '+%Y-%m-%d %H:%M:%S %Z')"
ZH_RELATIVE="${ZH_TARGET#$ICLOUD_DIR/}"
EN_RELATIVE="${EN_TARGET#$ICLOUD_DIR/}"

if [[ "$UPDATE_LATEST" == "1" ]]; then
  write_latest_index "$LATEST_INDEX"
  LATEST_STATUS="$LATEST_INDEX"
else
  LATEST_STATUS="skipped (set HORIZON_UPDATE_LATEST=1 to update)"
fi

echo "Copied Chinese file: $ZH_TARGET"
echo "Copied English file: $EN_TARGET"
echo "Copied posts dir: $COPIED_POSTS"
echo "Latest index path: $LATEST_STATUS"
echo "Final output count: ${FINAL_OUTPUT_COUNT:-unknown}"
echo "Enrichment counts: full=${FULL_ENRICHMENT_COUNT:-unknown}, brief=${BRIEF_ENRICHMENT_COUNT:-unknown}, skipped=${SKIPPED_ENRICHMENT_COUNT:-unknown}"
echo "iCloud export completed"
