"""Dashboard API - FastAPI backend for the Control Center."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import settings
from .process import get_process_manager
from .scheduler import get_scheduler
from .pipeline_status import get_pipeline_status, prewarm_status_caches
from .server_control import dashboard_status as get_dashboard_server_status
from .sheet_rotation import (
    approve_pending_rotation,
    get_sheet_rotation_manager,
    get_sheet_rotation_snapshot,
)

# Add Bots paths for shared scraper/common modules
sys.path.insert(0, str(settings.BOTS_DIR))
sys.path.insert(0, str(settings.BOTS_DIR / "maps_scraper"))

# Now import progress_status from the injected path
try:
    from progress_status import get_progress_status
    from config import get_dashboard_config as get_scraper_config
    from config import save_dashboard_config as save_scraper_config
    from config import save_location_sheet_sync as save_scraper_location_sync
    from config import save_location_source_settings as save_scraper_location_source_settings
    from config import sync_location_source_now as sync_scraper_location_source_now
    from common.system_config import get_system_config, save_system_config
    from common.config import OUTPUT_WEBHOOK_URL, SOURCE_WEBHOOK_URL, WEBHOOK_URL, WEBHOOK_SECRET
    from common.sheets_guard import get_sheets_guard_status
except ImportError as e:
    print(f"Warning: Could not import progress_status: {e}")
    def get_progress_status():
        return {"error": "progress_status module not found", "has_progress": False}
    def get_scraper_config():
        return {"error": "scraper config module not found"}
    def save_scraper_config(_payload):
        raise RuntimeError("scraper config module not found")
    def save_scraper_location_sync(*_args, **_kwargs):
        raise RuntimeError("scraper config module not found")
    def save_scraper_location_source_settings(*_args, **_kwargs):
        raise RuntimeError("scraper config module not found")
    def sync_scraper_location_source_now(*_args, **_kwargs):
        raise RuntimeError("scraper config module not found")
    def get_system_config(*_args, **_kwargs):
        return {"error": "system config module not found"}
    def save_system_config(*_args, **_kwargs):
        raise RuntimeError("system config module not found")
    def get_sheets_guard_status():
        return {}
    WEBHOOK_URL = ""
    SOURCE_WEBHOOK_URL = ""
    OUTPUT_WEBHOOK_URL = ""
    WEBHOOK_SECRET = ""


# =============================================================================
# APP SETUP
# =============================================================================

app = FastAPI(title="FixMyPresence Control Center", version="1.0.0")

# Mount static files and templates
DASHBOARD_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=DASHBOARD_DIR / "static"), name="static")
templates = Jinja2Templates(directory=DASHBOARD_DIR / "templates")


try:
    from auto_scaler import start_background_loop as _start_auto_scaler, stop_background_loop as _stop_auto_scaler, record_manual_change as _record_manual_change
except ImportError:
    def _start_auto_scaler(): pass
    def _stop_auto_scaler(): pass
    def _record_manual_change(): pass


@app.on_event("startup")
async def startup_event():
    get_sheet_rotation_manager().start()
    prewarm_status_caches()
    _start_auto_scaler()


@app.on_event("shutdown")
async def shutdown_event():
    get_sheet_rotation_manager().stop()
    _stop_auto_scaler()


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class StartRequest(BaseModel):
    mode: str = "manual"  # "manual" | "cycle"

class CleanRequest(BaseModel):
    mode: str = "logs"  # "logs" | "progress" | "outputs" | "full"

class ConfigRequest(BaseModel):
    run_minutes: Optional[int] = None
    rest_minutes: Optional[int] = None


class ScraperConfigRequest(BaseModel):
    runtime: dict = {}
    guardrails: dict = {}
    locations: list[str] = []
    location_source: dict = {}
    keyword_categories: list[list[str]] = []


class ScraperLocationSyncRequest(BaseModel):
    sheet_url: str
    tab_names: list[str] = []
    column_name: str
    active_tab_index: int = 0
    auto_advance_enabled: bool = False
    auto_advance_delay_minutes: int = 30
    advance: bool = False


class SystemConfigRequest(BaseModel):
    watchers: dict = {}
    pipeline_sheets: dict = {}
    delivery_sheets: dict = {}
    source_settlement: dict = {}
    output_target: dict = {}
    managed_sheets: dict = {}
    sheet_rotation: dict = {}
    enrichment: dict = {}


def _sheet_client():
    import gspread
    from google.oauth2.service_account import Credentials

    creds_path = os.path.expanduser(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json"))
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def _extract_sheet_id(sheet_url: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url or "")
    if not match:
        raise HTTPException(status_code=400, detail="Invalid Google Sheet URL.")
    return match.group(1)


def _column_index_from_ref(header: list[str], column_name: str) -> int:
    value = (column_name or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Column name is required.")

    normalized = value.strip().lower()
    for idx, cell in enumerate(header):
        if str(cell).strip().lower() == normalized:
            return idx

    upper = value.upper()
    if upper.isalpha():
        idx = 0
        for ch in upper:
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
        return idx - 1

    raise HTTPException(status_code=400, detail=f"Column '{value}' not found in the sheet header.")


def _build_csv_export_url(sheet_id: str, worksheet_gid: int) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={worksheet_gid}"


def _service_account_email() -> str:
    creds_path = os.path.expanduser(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "~/sheet-bot-key.json"))
    try:
        data = Path(creds_path).read_text(encoding="utf-8")
        return str(json.loads(data).get("client_email") or "").strip()
    except Exception:
        return ""


def _rotation_live_status(registry_snapshot: dict | None) -> dict:
    live = registry_snapshot.get("live_status") if isinstance(registry_snapshot, dict) else {}
    return live if isinstance(live, dict) else {}


def _merge_cached_system_live_status(config: dict, rotation_snapshot: dict | None) -> dict:
    if not isinstance(config, dict):
        return config

    merged = json.loads(json.dumps(config))
    registries = (
        rotation_snapshot.get("registries")
        if isinstance(rotation_snapshot, dict) and isinstance(rotation_snapshot.get("registries"), dict)
        else {}
    )

    pipeline_snapshot = registries.get("pipeline_sheets") if isinstance(registries.get("pipeline_sheets"), dict) else {}
    pipeline_live = _rotation_live_status(pipeline_snapshot)
    pipeline_cfg = merged.get("pipeline_sheets") if isinstance(merged.get("pipeline_sheets"), dict) else {}
    pipeline_sheets = pipeline_cfg.get("sheets") if isinstance(pipeline_cfg.get("sheets"), list) else []
    active_pipeline_key = str(pipeline_cfg.get("active_sheet_key") or "").strip()
    if pipeline_live:
        pipeline_cfg["active_status"] = pipeline_live
        pipeline_cfg["sheets"] = [
            {
                **sheet,
                "status": pipeline_live if str(sheet.get("key") or "").strip() == active_pipeline_key else sheet.get("status") or {},
            }
            for sheet in pipeline_sheets
        ]
        merged["pipeline_sheets"] = pipeline_cfg
        merged["delivery_sheets"] = pipeline_cfg

    source_snapshot = registries.get("source_settlement") if isinstance(registries.get("source_settlement"), dict) else {}
    source_live = _rotation_live_status(source_snapshot)
    source_cfg = merged.get("source_settlement") if isinstance(merged.get("source_settlement"), dict) else {}
    if source_live:
        source_cfg["active_status"] = source_live
        merged["source_settlement"] = source_cfg

    output_snapshot = registries.get("output_target") if isinstance(registries.get("output_target"), dict) else {}
    output_live = _rotation_live_status(output_snapshot)
    output_cfg = merged.get("output_target") if isinstance(merged.get("output_target"), dict) else {}
    if output_live:
        output_cfg["active_status"] = output_live
        merged["output_target"] = output_cfg

    return merged


def _parse_apps_script_sync_response(response_text: str) -> tuple[bool, str]:
    text = (response_text or "").strip()
    if not text:
        return False, "Empty Apps Script response."
    if text.lower().startswith("error:"):
        return False, text
    try:
        payload = json.loads(text)
    except Exception:
        return True, text

    status = str(payload.get("status") or "").strip().lower()
    if status in {"ok", "success"}:
        return True, text
    if status:
        message = payload.get("message") or text
        return False, str(message)
    return True, text


def _sync_pipeline_config_to_apps_script(config: dict) -> tuple[bool, str]:
    pipeline = ((config.get("pipeline_sheets") or {}).get("sheets") if isinstance(config, dict) else None) or []
    active_key = ((config.get("pipeline_sheets") or {}).get("active_sheet_key") if isinstance(config, dict) else None) or ""
    active = next((item for item in pipeline if item.get("key") == active_key), None)
    if not active or not WEBHOOK_URL:
        return False, "No active pipeline sheet or webhook URL configured."

    try:
        import httpx

        payload = {
            "token": WEBHOOK_SECRET,
            "action": "sync_pipeline_config",
            "pipeline": {
                "sheet_id": active.get("sheet_id"),
                "sheet_label": active.get("label"),
                "inbox_tab": active.get("inbox_tab"),
                "validated_tab": active.get("validated_tab"),
                "ultra_tab": active.get("ultra_tab"),
                "config_tab": active.get("config_tab"),
                "inbox_quota": active.get("inbox_quota"),
                "enrich_quota": active.get("enrich_quota"),
                "hydro_sheet_id": active.get("hydro_sheet_id"),
                "hydro_formula_tab": active.get("hydro_formula_tab"),
                "hydro_incomplete_tab": active.get("hydro_incomplete_tab"),
            },
        }
        timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(WEBHOOK_URL, headers={"Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
            return _parse_apps_script_sync_response(response.text)
    except Exception as exc:
        return False, str(exc)


def _sync_source_config_to_apps_script(config: dict) -> tuple[bool, str]:
    source_cfg = (config.get("source_settlement") if isinstance(config, dict) else None) or {}
    sheets = source_cfg.get("sheets") if isinstance(source_cfg.get("sheets"), list) else []
    active_key = source_cfg.get("active_sheet_key")
    source = next((item for item in sheets if item.get("key") == active_key), sheets[0] if sheets else None) or {}
    webhook_url = source.get("source_webhook_url") or SOURCE_WEBHOOK_URL
    if not source or not webhook_url:
        return False, "No source settlement target or source webhook URL configured."

    try:
        import httpx

        payload = {
            "token": WEBHOOK_SECRET,
            "action": "sync_source_sheet_config",
            "source": {
                "sheet_id": source.get("sheet_id"),
                "tabs_to_process": [
                    source.get("alpha_tab"),
                    source.get("alpha_high_tab"),
                    source.get("beta_tab"),
                    source.get("beta_high_tab"),
                    source.get("gamma_tab"),
                    source.get("gamma_high_tab"),
                ],
            },
        }
        timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(webhook_url, headers={"Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
            return _parse_apps_script_sync_response(response.text)
    except Exception as exc:
        return False, str(exc)


def _sync_output_config_to_apps_script(config: dict) -> tuple[bool, str]:
    output_cfg = (config.get("output_target") if isinstance(config, dict) else None) or {}
    sheets = output_cfg.get("sheets") if isinstance(output_cfg.get("sheets"), list) else []
    active_key = output_cfg.get("active_sheet_key")
    output = next((item for item in sheets if item.get("key") == active_key), sheets[0] if sheets else None) or {}
    if not output or not OUTPUT_WEBHOOK_URL:
        return False, "No output target or output webhook URL configured."

    try:
        import httpx

        payload = {
            "token": WEBHOOK_SECRET,
            "action": "sync_output_sheet_config",
            "output": {
                "sheet_id": output.get("sheet_id"),
                "tab_name": output.get("tab_name"),
            },
        }
        timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(OUTPUT_WEBHOOK_URL, headers={"Content-Type": "application/json"}, json=payload)
            response.raise_for_status()
            return _parse_apps_script_sync_response(response.text)
    except Exception as exc:
        return False, str(exc)


def _project_python() -> str:
    venv_python = settings.PROJECT_ROOT / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _run_subprocess_capture(cmd: list[str], *, env_overrides: Optional[dict] = None, timeout: int = 240) -> dict:
    env = os.environ.copy()
    if env_overrides:
        env.update({key: str(value) for key, value in env_overrides.items()})

    completed = subprocess.run(
        cmd,
        cwd=settings.PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part).strip()
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": output,
    }


# =============================================================================
# ROUTES
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Overview page."""
    return templates.TemplateResponse(request, "overview.html", {"active_page": "overview"})


@app.get("/scraper", response_class=HTMLResponse)
async def scraper_page(request: Request):
    """Scraper controls and progress."""
    return templates.TemplateResponse(request, "scraper.html", {"active_page": "scraper"})


@app.get("/enrichment", response_class=HTMLResponse)
async def enrichment_page(request: Request):
    """Enrichment pipeline status."""
    return templates.TemplateResponse(request, "enrichment.html", {"active_page": "enrichment"})


@app.get("/robot", response_class=HTMLResponse)
async def robot_page(request: Request):
    """Robot control room."""
    return templates.TemplateResponse(request, "robot.html", {"active_page": "robot"})


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    """System configuration."""
    return templates.TemplateResponse(request, "config.html", {"active_page": "config"})


@app.get("/locations", response_class=HTMLResponse)
async def locations_page(request: Request):
    """Location generator."""
    return templates.TemplateResponse(request, "locations.html", {"active_page": "locations"})


@app.get("/logs/view", response_class=HTMLResponse)
async def logs_page(request: Request):
    """Unified log viewer."""
    return templates.TemplateResponse(request, "logs.html", {"active_page": "logs"})


@app.get("/status")
async def get_status():
    """
    Unified Status Endpoint.
    Returns process info, scheduler state, scraper progress, and full pipeline snapshot.
    Single source of truth.
    """
    pm = get_process_manager()
    scheduler = get_scheduler()

    process_info = await run_in_threadpool(pm.get_info)
    scheduler_state = await run_in_threadpool(scheduler.get_state)
    scraper_progress = await run_in_threadpool(get_progress_status)
    pipeline = await run_in_threadpool(get_pipeline_status, process_info, scheduler_state, scraper_progress)
    sheets_guard = await run_in_threadpool(get_sheets_guard_status)

    return {
        "timestamp": datetime.now().isoformat(),
        "process": process_info,
        "scheduler": scheduler_state,
        "scraper": scraper_progress,
        "pipeline": pipeline,
        "meta": {
            "sheet_rotation": (pipeline or {}).get("sheet_rotation") if isinstance(pipeline, dict) else {},
            "sheets_guard": sheets_guard,
        },
    }


@app.post("/control/start")
async def control_start(request: StartRequest):
    """
    Start the controller.
    
    Modes:
    - "manual": Starts controller once. Scheduler stays disabled.
    - "cycle": Enables scheduler + starts controller (4h run / 2h rest loop).
    """
    if request.mode == "cycle":
        scheduler = get_scheduler()
        return scheduler.start_cycle()
    else:
        # Manual start - keep scheduler disabled
        pm = get_process_manager()
        return pm.start()


@app.post("/control/scraper/approve-next-category")
async def control_approve_next_category():
    """
    Explicitly approve advancing the Maps Scraper to the next category.
    Starts the controller in one-shot mode with category approval enabled.
    """
    pm = get_process_manager()
    return pm.start(extra_flags=["--approve-next-category"])


@app.post("/control/stop")
async def control_stop():
    """
    Graceful stop + disable scheduler (manual override).
    """
    scheduler = get_scheduler()
    return scheduler.manual_stop()


@app.post("/control/kill")
async def control_kill():
    """
    Immediate hard kill.
    Also disables scheduler.
    """
    scheduler = get_scheduler()
    scheduler._enabled = False
    scheduler._stop_event.set()
    
    pm = get_process_manager()
    return pm.kill()


@app.post("/control/dashboard/shutdown")
async def control_dashboard_shutdown():
    """Shutdown the dashboard backend through the dedicated control script."""
    control_script = settings.PROJECT_ROOT / "scripts" / "dashboard_server.py"
    subprocess.Popen(
        [_project_python(), str(control_script), "stop", "--force", "--delay", "1.0"],
        cwd=settings.PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        "success": True,
        "message": "Dashboard shutdown requested. This page will disconnect in a moment.",
    }


@app.post("/control/dashboard/restart")
async def control_dashboard_restart():
    """Restart the dashboard backend through the dedicated control script."""
    control_script = settings.PROJECT_ROOT / "scripts" / "dashboard_server.py"
    subprocess.Popen(
        [_project_python(), str(control_script), "restart", "--delay", "1.0"],
        cwd=settings.PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        "success": True,
        "message": "Dashboard restart requested. This page should reconnect shortly.",
    }


@app.post("/control/clean")
async def control_clean(request: CleanRequest):
    """
    Clean system files.
    
    Modes:
    - "logs": Clear log files only.
    - "progress": Reset scraper progress (start fresh).
    - "outputs": Clear batch outputs and temporary files.
    - "full": Safe clean (logs + progress + outputs, keeps sheet data).
    - "nuclear": EVERYTHING - leads, destination sheets, final output, all progress.
    
    Safety: Only runs if controller is stopped.
    """
    pm = get_process_manager()
    
    # Safety gate
    if pm.is_running():
        raise HTTPException(
            status_code=409,
            detail="Cannot clean while controller is running. Stop it first."
        )
    
    # Check for stale PID
    pm._check_stale_pid()
    
    cleaned = []
    mode = request.mode
    
    # Nuclear mode - uses clean_pipeline.py --all
    if mode == "nuclear":
        # Run clean_pipeline.py --all (clears pipeline, destination, output sheets,
        # local scraper history/state, summaries, and logs)
        clean_script = settings.CONTROLLER_DIR / "clean_pipeline.py"
        if clean_script.exists():
            result = subprocess.run(
                [_project_python(), str(clean_script), "--all"],
                capture_output=True,
                text=True,
                cwd=str(settings.PROJECT_ROOT),
                timeout=120  # 2 min timeout for sheet operations
            )
            if result.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=result.stderr.strip() or result.stdout.strip() or "Nuclear clean failed.",
                )
            cleaned.append("pipeline sheets")
            cleaned.append("destination sheets")
            cleaned.append("final output sheet")
            cleaned.append("local scraper history/state")
            cleaned.append("scraper v3 summaries")
            cleaned.append("logs")
        
        return {
            "success": True,
            "mode": mode,
            "cleaned": cleaned,
            "warning": "NUCLEAR CLEAN: All data has been deleted!"
        }
    
    # Regular modes
    if mode in ("logs", "full"):
        # Clear logs
        if settings.SYSTEM_LOG.exists():
            settings.SYSTEM_LOG.unlink()
            cleaned.append("logs/system.log")
    
    if mode in ("progress", "full"):
        # Run clean_pipeline.py --kill --reset-enricher --reset-ultra
        clean_script = settings.CONTROLLER_DIR / "clean_pipeline.py"
        if clean_script.exists():
            result = subprocess.run(
                ["python3", str(clean_script), "--kill", "--reset-enricher", "--reset-ultra"],
                capture_output=True,
                text=True,
                cwd=str(settings.PROJECT_ROOT)
            )
            cleaned.append("controller progress state")
            if result.returncode != 0:
                cleaned.append(f"(with warnings: {result.stderr[:200]})")
    
    if mode in ("outputs", "full"):
        # Clear batch files
        output_dir = settings.BOTS_DIR / "maps_scraper" / "output"
        if output_dir.exists():
            for batch_file in output_dir.glob("leads_batch_*.csv"):
                batch_file.unlink()
                cleaned.append(f"output/{batch_file.name}")
            
            # Clear progress file
            for filename in (
                "completed_tasks.jsonl",
                "pending_batch.json",
                "scraper_health.json",
                "scraper_callouts.jsonl",
                "scraper_stats.json",
            ):
                path = output_dir / filename
                if path.exists():
                    path.unlink()
                    cleaned.append(f"output/{filename}")
    
    return {
        "success": True,
        "mode": mode,
        "cleaned": cleaned
    }


@app.get("/logs")
async def get_logs(
    lines: int = Query(100, ge=10, le=1000),
    format: str = Query("json", pattern="^(json|text)$"),
):
    """
    Get last N lines of system log.
    Distinguishes between file missing vs empty.
    """
    if not settings.SYSTEM_LOG.exists():
        if format == "text":
            return PlainTextResponse("", media_type="text/plain")
        return {"lines": [], "total": 0, "file_missing": True}
    
    try:
        content = settings.SYSTEM_LOG.read_text()
        if not content.strip():
            if format == "text":
                return PlainTextResponse("", media_type="text/plain")
            return {"lines": [], "total": 0, "file_missing": False}
        
        all_lines = content.strip().split("\n")
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        if format == "text":
            return PlainTextResponse(
                "\n".join(tail),
                media_type="text/plain",
                headers={"Content-Disposition": "attachment; filename=system.log"},
            )
        
        return {
            "lines": tail,
            "total": len(all_lines),
            "showing": len(tail),
            "file_missing": False
        }
    except Exception as e:
        return {"error": str(e), "lines": [], "file_missing": False}


@app.post("/config")
async def update_config(request: ConfigRequest):
    """
    Update scheduler configuration.
    """
    scheduler = get_scheduler()
    scheduler.configure(
        run_minutes=request.run_minutes,
        rest_minutes=request.rest_minutes
    )
    return {
        "success": True,
        "config": scheduler.get_config()
    }


@app.get("/config/scheduler")
async def get_scheduler_config():
    """Return scheduler configuration without the heavy unified status payload."""
    scheduler = get_scheduler()
    return {
        "success": True,
        "config": scheduler.get_config(),
        "state": scheduler.get_state(),
    }


@app.get("/config/scraper")
async def get_scraper_config_endpoint():
    """Return dashboard-managed scraper configuration."""
    config = get_scraper_config()
    if isinstance(config, dict):
        meta = config.get("meta") if isinstance(config.get("meta"), dict) else {}
        meta["service_account_email"] = _service_account_email()
        config["meta"] = meta
    return {
        "success": True,
        "config": config,
    }


@app.post("/config/scraper")
async def update_scraper_config(request: ScraperConfigRequest):
    """Persist dashboard-managed scraper configuration overrides."""
    config = save_scraper_config(request.model_dump())
    _record_manual_change()  # reset auto-scaler 30-min stability clock
    meta = config.get("meta") if isinstance(config.get("meta"), dict) else {}
    meta["service_account_email"] = _service_account_email()
    config["meta"] = meta
    return {
        "success": True,
        "config": config,
        "message": "Scraper configuration saved. New values apply to future scraper starts.",
    }


@app.post("/config/scraper/locations/sync")
async def sync_scraper_locations(request: ScraperLocationSyncRequest):
    """Sync locations from the configured sheet-backed source, optionally advancing to the next tab."""
    tab_names = [str(tab).strip() for tab in request.tab_names if str(tab).strip()]
    if not request.sheet_url.strip():
        raise HTTPException(status_code=400, detail="Sheet URL is required.")
    if not tab_names:
        raise HTTPException(status_code=400, detail="At least one source tab is required.")
    if not request.column_name.strip():
        raise HTTPException(status_code=400, detail="Column name is required.")

    active_index = max(0, min(int(request.active_tab_index or 0), len(tab_names) - 1))

    save_scraper_location_source_settings({
        "mode": "sheet_csv",
        "sheet_url": request.sheet_url,
        "tab_names": tab_names,
        "tab_name": tab_names[active_index],
        "active_tab_index": active_index,
        "auto_advance_enabled": request.auto_advance_enabled,
        "auto_advance_delay_minutes": request.auto_advance_delay_minutes,
        "column_name": request.column_name,
    })

    try:
        config = sync_scraper_location_source_now(advance=request.advance, respect_delay=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not sync locations: {exc}") from exc

    meta = config.get("meta") if isinstance(config.get("meta"), dict) else {}
    meta["service_account_email"] = _service_account_email()
    config["meta"] = meta
    source = config.get("location_source") if isinstance(config.get("location_source"), dict) else {}
    active_tab = str(source.get("active_tab_name") or source.get("tab_name") or "").strip()
    resolved_locations = config.get("location_resolved_locations") if isinstance(config.get("location_resolved_locations"), list) else []

    return {
        "success": True,
        "message": f"Fetched {len(resolved_locations)} locations from {active_tab}.{request.column_name}.",
        "locations": resolved_locations[:50],
        "total": len(resolved_locations),
        "config": config,
    }


@app.post("/config/scraper/locations/advance")
async def advance_scraper_locations(request: ScraperLocationSyncRequest):
    """Advance the active sheet-backed source tab, sync it, and persist the new cursor."""
    request_data = request.model_dump()
    request_data["advance"] = True
    return await sync_scraper_locations(ScraperLocationSyncRequest(**request_data))


@app.get("/config/system")
async def get_system_config_endpoint():
    """Return shared system config for watcher and delivery-sheet management."""
    config = await run_in_threadpool(get_system_config, include_live=False)
    rotation_snapshot = await run_in_threadpool(get_sheet_rotation_snapshot)
    config = _merge_cached_system_live_status(config, rotation_snapshot)
    if isinstance(config, dict):
        meta = config.get("meta") if isinstance(config.get("meta"), dict) else {}
        meta["service_account_email"] = _service_account_email()
        meta["sheet_rotation"] = rotation_snapshot
        config["meta"] = meta
    return {
        "success": True,
        "config": config,
    }


@app.post("/config/system")
async def update_system_config(request: SystemConfigRequest):
    """Persist shared watcher and delivery-sheet config."""
    config = save_system_config(request.model_dump())
    await run_in_threadpool(get_sheet_rotation_manager().run_once)
    meta = config.get("meta") if isinstance(config.get("meta"), dict) else {}
    meta["service_account_email"] = _service_account_email()
    meta["sheet_rotation"] = await run_in_threadpool(get_sheet_rotation_snapshot)
    pipeline_synced, pipeline_message = _sync_pipeline_config_to_apps_script(config)
    source_synced, source_message = _sync_source_config_to_apps_script(config)
    output_synced, output_message = _sync_output_config_to_apps_script(config)
    meta["apps_script_sync_ok"] = pipeline_synced and source_synced and output_synced
    meta["apps_script_sync_message"] = " | ".join(
        message for message in [pipeline_message, source_message, output_message] if message
    )
    meta["pipeline_apps_script_sync_ok"] = pipeline_synced
    meta["pipeline_apps_script_sync_message"] = pipeline_message
    meta["source_apps_script_sync_ok"] = source_synced
    meta["source_apps_script_sync_message"] = source_message
    meta["output_apps_script_sync_ok"] = output_synced
    meta["output_apps_script_sync_message"] = output_message
    config["meta"] = meta
    return {
        "success": True,
        "config": config,
        "message": "System config saved. Pipeline, source settlement, and output targets are updated.",
    }


@app.post("/control/sheets/approve-rotation/{registry}")
async def approve_sheet_rotation(registry: str):
    """Approve activation of a prepared replacement sheet for a registry."""
    allowed = {"pipeline_sheets", "source_settlement", "output_target"}
    if registry not in allowed:
        raise HTTPException(status_code=400, detail="Unknown sheet registry.")
    snapshot = await run_in_threadpool(approve_pending_rotation, registry)
    await run_in_threadpool(get_sheet_rotation_manager().run_once)
    return {
        "success": True,
        "message": f"Approved pending rotation for {registry}.",
        "sheet_rotation": snapshot,
    }


@app.post("/control/enrichment/linkedin/watcher-check")
async def enrichment_linkedin_watcher_check():
    """Run a single LinkedIn enrichment watcher tick."""
    cmd = [_project_python(), str(settings.CONTROLLER_DIR / "enrichment_watcher.py"), "--once"]
    result = _run_subprocess_capture(cmd)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["output"] or "Watcher check failed.")
    return {
        "success": True,
        "message": "LinkedIn watcher check completed.",
        "output": result["output"],
    }


@app.post("/control/enrichment/linkedin/force-drain")
async def enrichment_linkedin_force_drain():
    """Force a LinkedIn watcher tick to drain immediately if backlog exists."""
    cmd = [_project_python(), str(settings.CONTROLLER_DIR / "enrichment_watcher.py"), "--once"]
    result = _run_subprocess_capture(
        cmd,
        env_overrides={
            "MIN_BACKLOG_TO_DRAIN": "1",
            "MAX_WAIT_BEFORE_FORCED_DRAIN_MINUTES": "0",
        },
    )
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["output"] or "Force drain failed.")
    return {
        "success": True,
        "message": "Force-drain check completed.",
        "output": result["output"],
    }


@app.post("/control/enrichment/linkedin/reset-pointer")
async def enrichment_linkedin_reset_pointer():
    """Reset the LinkedIn enrichment pointer back to row 2."""
    cmd = [_project_python(), str(settings.BOTS_DIR / "enricher" / "enrich_linkedin.py"), "--reset-pointer"]
    result = _run_subprocess_capture(cmd)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["output"] or "Pointer reset failed.")
    return {
        "success": True,
        "message": "LinkedIn enrichment pointer reset.",
        "output": result["output"],
    }


@app.post("/control/enrichment/linkedin/reset-hard")
async def enrichment_linkedin_reset_hard():
    """Reset the pointer and clear LinkedIn enrichment result columns."""
    cmd = [
        _project_python(),
        str(settings.BOTS_DIR / "enricher" / "enrich_linkedin.py"),
        "--reset-pointer",
        "--reset-hard",
    ]
    result = _run_subprocess_capture(cmd)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["output"] or "Hard reset failed.")
    return {
        "success": True,
        "message": "LinkedIn enrichment hard reset completed.",
        "output": result["output"],
    }


@app.post("/control/enrichment/scraper-v3/watcher-check")
async def enrichment_scraper_v3_watcher_check():
    """Run a single scraper_v3 watcher tick."""
    cmd = [_project_python(), str(settings.CONTROLLER_DIR / "scraper_v3_watcher.py"), "--once"]
    result = _run_subprocess_capture(cmd)
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["output"] or "Scraper V3 watcher check failed.")
    return {
        "success": True,
        "message": "Scraper V3 watcher check completed.",
        "output": result["output"],
    }


@app.post("/control/enrichment/scraper-v3/force-run")
async def enrichment_scraper_v3_force_run():
    """Force a scraper_v3 watcher tick to launch immediately if any backlog exists."""
    cmd = [_project_python(), str(settings.CONTROLLER_DIR / "scraper_v3_watcher.py"), "--once"]
    result = _run_subprocess_capture(
        cmd,
        env_overrides={
            "MIN_ALPHA_BETA_BACKLOG_TO_RUN": "1",
            "SCRAPER_V3_MAX_WAIT_MINUTES": "0",
        },
    )
    if not result["ok"]:
        raise HTTPException(status_code=500, detail=result["output"] or "Scraper V3 force run failed.")
    return {
        "success": True,
        "message": "Scraper V3 force-run check completed.",
        "output": result["output"],
    }


@app.post("/control/enrichment/scraper-v3/reset-checkpoint")
async def enrichment_scraper_v3_reset_checkpoint():
    """Clear the latest scraper_v3 resume checkpoint so the next run starts fresh."""
    checkpoint_path = settings.BOTS_DIR / "enricher" / "outputs" / "checkpoints" / "checkpoint_latest.json"
    try:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text("{}\n", encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not clear scraper_v3 checkpoint: {exc}")
    return {
        "success": True,
        "message": "Scraper V3 latest checkpoint cleared.",
        "output": str(checkpoint_path),
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "dashboard_server": get_dashboard_server_status(),
    }


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
