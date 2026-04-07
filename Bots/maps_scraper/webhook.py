from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
from typing import Any, Dict, Iterable, List

_SCRAPER_DIR = os.path.dirname(__file__)
_BOTS_DIR = os.path.dirname(_SCRAPER_DIR)
if _BOTS_DIR not in sys.path:
    sys.path.insert(0, _BOTS_DIR)

from maps_scraper.utils import Lead
from common.google_client import GoogleClient
from common.system_config import get_active_delivery_sheet, get_delivery_sheet_capacity_status, get_system_config


def _lead_to_row(lead: Lead) -> List[str]:
    return [lead.name, lead.website or "", lead.location, lead.keyword, lead.place_url or "", lead.status]


def _run_stage2_sorter(target: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "action": "run_stage2_sorter",
        "pipeline": {
            "sheet_id": target.get("sheet_id"),
            "sheet_label": target.get("label"),
            "inbox_tab": target.get("inbox_tab"),
            "validated_tab": target.get("validated_tab"),
            "ultra_tab": target.get("ultra_tab"),
            "config_tab": target.get("config_tab"),
            "inbox_quota": target.get("inbox_quota"),
            "enrich_quota": target.get("enrich_quota"),
            "hydro_sheet_id": target.get("hydro_sheet_id"),
            "hydro_formula_tab": target.get("hydro_formula_tab"),
            "hydro_incomplete_tab": target.get("hydro_incomplete_tab"),
        },
    }
    result = GoogleClient.post_sync_detailed(payload)
    parsed = result.get("parsed") or {}
    return {
        "ok": bool(result.get("ok")),
        "message": parsed.get("message") or result.get("text") or "",
        "status_code": result.get("status_code"),
        "response": parsed if isinstance(parsed, dict) else {},
    }


def send_batch_to_webhook(batch_number: int, leads: Iterable[Lead]) -> Dict[str, Any]:
    leads_list = list(leads)
    ts = datetime.now(timezone.utc).isoformat()
    batch_id = f"batch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{batch_number:03d}"

    rows = []
    for ld in leads_list:
        base = [ld.name, ld.website or "", ld.location, ld.keyword, ld.place_url or ""]
        rows.append(base + [ts, batch_id, ld.status])

    config = get_system_config(include_live=False)
    target = get_active_delivery_sheet(config)
    if not target:
        return {
            "ok": False,
            "blocked": True,
            "reason": "no_active_delivery_sheet",
            "message": "No active pipeline sheet is configured.",
            "sheet_status": {},
        }

    capacity = get_delivery_sheet_capacity_status(target, pending_rows=len(rows), config=config)
    if not capacity.get("ok"):
        return {
            "ok": False,
            "blocked": False,
            "reason": "delivery_sheet_check_failed",
            "message": capacity.get("error") or "Could not inspect delivery sheet capacity.",
            "sheet_status": capacity,
        }

    if capacity.get("blocked"):
        return {
            "ok": False,
            "blocked": True,
            "reason": "sheet_capacity_reached",
            "message": (
                f"Pipeline sheet {target.get('label')} ({target.get('inbox_tab')}) "
                f"hit the configured stop threshold at projected row {capacity.get('projected_rows')}."
            ),
            "sheet_status": capacity,
        }

    try:
        payload = {
            "batch_id": batch_id,
            "rows": rows,
            "pipeline": {
                "sheet_id": target.get("sheet_id"),
                "sheet_label": target.get("label"),
                "inbox_tab": target.get("inbox_tab"),
                "validated_tab": target.get("validated_tab"),
                "ultra_tab": target.get("ultra_tab"),
                "config_tab": target.get("config_tab"),
                "inbox_quota": target.get("inbox_quota"),
                "enrich_quota": target.get("enrich_quota"),
                "hydro_sheet_id": target.get("hydro_sheet_id"),
                "hydro_formula_tab": target.get("hydro_formula_tab"),
                "hydro_incomplete_tab": target.get("hydro_incomplete_tab"),
            },
        }
        post_result = GoogleClient.post_sync_detailed(payload)
        if not post_result.get("ok"):
            raise RuntimeError("Apps Script webhook rejected the batch delivery.")
        parsed = post_result.get("parsed") or {}
        moved = 0
        try:
            moved = int(parsed.get("moved") or 0)
        except Exception:
            moved = 0

        stage2 = None
        if moved > 0:
            stage2 = _run_stage2_sorter(target)
        print(f"[Delivery] OK (batch_id={batch_id} → {target.get('label')} / {target.get('inbox_tab')})")
        return {
            "ok": True,
            "blocked": False,
            "reason": None,
            "message": "Batch delivered through Apps Script.",
            "sheet_status": capacity,
            "apps_script": parsed if isinstance(parsed, dict) else {},
            "stage2": stage2,
        }
    except Exception as exc:
        print(f"[Delivery] FAILED: batch {batch_number} → {exc}")
        return {
            "ok": False,
            "blocked": False,
            "reason": "delivery_write_failed",
            "message": str(exc),
            "sheet_status": capacity,
        }
