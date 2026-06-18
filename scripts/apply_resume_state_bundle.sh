#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: bash scripts/apply_resume_state_bundle.sh <bundle.tar.gz> [project_root]"
  exit 2
fi

bundle="$1"
project_root="${2:-$(pwd)}"

if [[ ! -f "$bundle" ]]; then
  echo "Bundle not found: $bundle"
  exit 1
fi

if [[ ! -d "$project_root" ]]; then
  echo "Project root not found: $project_root"
  exit 1
fi

cd "$project_root"

timestamp="$(date +%Y%m%d_%H%M%S)"
backup_dir="deploy/resume-state/backups"
mkdir -p "$backup_dir"
backup="$backup_dir/pre-resume-state-$timestamp.tar.gz"

backup_paths=(
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

existing_backup_paths=()
for path in "${backup_paths[@]}"; do
  if [[ -e "$path" ]]; then
    existing_backup_paths+=("$path")
  fi
done

if [[ ${#existing_backup_paths[@]} -gt 0 ]]; then
  tar \
    --exclude="Bots/maps_scraper/output/parallel_scraper.lock" \
    --exclude="controller/*.lock" \
    --exclude="controller/logs" \
    -czf "$backup" \
    "${existing_backup_paths[@]}"
  echo "Backed up existing VM state:"
  echo "  $backup"
else
  echo "No existing VM state files found to back up."
fi

tar -xzf "$bundle" -C "$project_root"

rm -f \
  "Bots/maps_scraper/output/parallel_scraper.lock" \
  "dashboard_server.pid" \
  "dashboard_controller.pid" \
  dashboard_*watcher.pid

echo "Applied resume-state bundle:"
echo "  $bundle"
echo
echo "Next:"
echo "  systemctl restart fixmypresence-dashboard"
