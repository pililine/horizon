#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

HOURS="${1:-12}"

echo "Running Horizon local daily report"
echo "Hours: $HOURS"

if command -v jq >/dev/null 2>&1; then
  MODEL="$(jq -r '.ai.model // "unknown"' data/config.json)"
  ENRICHMENT_MODE="$(jq -r '.ai.enrichment_mode // "unknown"' data/config.json)"
  echo "Model: $MODEL"
  echo "Enrichment mode: $ENRICHMENT_MODE"
fi

echo "Command: .venv/bin/horizon --hours $HOURS"

.venv/bin/horizon --hours "$HOURS"
