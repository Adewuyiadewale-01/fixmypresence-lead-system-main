#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

timestamp="$(date +%Y%m%d_%H%M%S)"
out_dir="${1:-deploy/resume-state}"
mkdir -p "$out_dir"
bundle="$out_dir/fixmypresence-resume-state-$timestamp.tar.gz"

include_paths=(
  "Bots/common/system_config.json"
  "Bots/maps_scraper/config_overrides.json"
  "Bots/maps_scraper/output"
  "Bots/enricher/outputs/checkpoints"
  "Bots/enricher/outputs/results"
  "Bots/enricher/outputs/summaries"
  "controller/enrichment_run_state.json"
  "controller/enrichment_watcher_state.json"
  "controller/sheet_rotation_state.json"
  "controller/sheets_rate_gate_state.json"
)

existing_paths=()
for path in "${include_paths[@]}"; do
  if [[ -e "$path" ]]; then
    existing_paths+=("$path")
  fi
done

if [[ ${#existing_paths[@]} -eq 0 ]]; then
  echo "No resume state files found."
  exit 1
fi

tar \
  --exclude="Bots/maps_scraper/output/parallel_scraper.lock" \
  --exclude="Bots/maps_scraper/output/scraper_health.json" \
  --exclude="controller/*.lock" \
  --exclude="controller/*.pid" \
  --exclude="controller/logs" \
  --exclude="dashboard_server.pid" \
  --exclude="dashboard_controller.pid" \
  --exclude="dashboard_*watcher.pid" \
  --exclude="*.log" \
  -czf "$bundle" \
  "${existing_paths[@]}"

echo "Created resume-state bundle:"
echo "  $bundle"
echo
echo "Apply on the VM after code sync and while the dashboard service is stopped:"
echo "  bash scripts/apply_resume_state_bundle.sh $bundle /opt/fixmypresence"
