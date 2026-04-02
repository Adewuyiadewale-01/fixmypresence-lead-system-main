"""Dashboard API - FastAPI backend for the Control Center."""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from . import settings
from .process import get_process_manager
from .scheduler import get_scheduler

# Add Bots path to import progress_status
sys.path.insert(0, str(settings.BOTS_DIR / "maps_scraper"))

# Now import progress_status from the injected path
try:
    from progress_status import get_progress_status
except ImportError as e:
    print(f"Warning: Could not import progress_status: {e}")
    def get_progress_status():
        return {"error": "progress_status module not found", "has_progress": False}


# =============================================================================
# APP SETUP
# =============================================================================

app = FastAPI(title="FixMyPresence Control Center", version="1.0.0")

# Mount static files
DASHBOARD_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=DASHBOARD_DIR / "static"), name="static")


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class StartRequest(BaseModel):
    mode: str = "manual"  # "manual" | "cycle"

class CleanRequest(BaseModel):
    mode: str = "logs"  # "logs" | "progress" | "outputs" | "full"

class ConfigRequest(BaseModel):
    run_hours: Optional[int] = None
    rest_hours: Optional[int] = None


# =============================================================================
# ROUTES
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the dashboard HTML."""
    template_path = DASHBOARD_DIR / "templates" / "index.html"
    if template_path.exists():
        return HTMLResponse(content=template_path.read_text())
    return HTMLResponse(content="<h1>Dashboard template not found</h1>")


@app.get("/status")
async def get_status():
    """
    Unified Status Endpoint.
    Returns process info, scheduler state, and scraper progress.
    Single source of truth.
    """
    pm = get_process_manager()
    scheduler = get_scheduler()
    
    return {
        "timestamp": datetime.now().isoformat(),
        "process": pm.get_info(),
        "scheduler": scheduler.get_state(),
        "scraper": get_progress_status()
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


@app.post("/control/clean")
async def control_clean(request: CleanRequest):
    """
    Clean system files.
    
    Modes:
    - "logs": Clear log files only.
    - "progress": Reset scraper progress (start fresh).
    - "outputs": Clear batch outputs and temporary files.
    - "full": Safe clean (logs + progress + outputs, keeps leads data).
    - "nuclear": EVERYTHING - leads, destination sheets, all progress.
    
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
        # Clear logs
        if settings.SYSTEM_LOG.exists():
            settings.SYSTEM_LOG.unlink()
            cleaned.append("logs/system.log")
        
        # Clear ALL scraper progress files
        output_dir = settings.BOTS_DIR / "maps_scraper" / "output"
        if output_dir.exists():
            for f in output_dir.glob("*.csv"):
                f.unlink()
                cleaned.append(f"output/{f.name}")
            for f in output_dir.glob("*.json"):
                f.unlink()
                cleaned.append(f"output/{f.name}")
            for f in output_dir.glob("*.jsonl"):
                f.unlink()
                cleaned.append(f"output/{f.name}")
        
        # Run clean_pipeline.py --all (clears sheets too)
        clean_script = settings.CONTROLLER_DIR / "clean_pipeline.py"
        if clean_script.exists():
            result = subprocess.run(
                ["python3", str(clean_script), "--all"],
                capture_output=True,
                text=True,
                cwd=str(settings.PROJECT_ROOT),
                timeout=120  # 2 min timeout for sheet operations
            )
            cleaned.append("all sheets and destination tabs")
            if result.returncode != 0:
                cleaned.append(f"(with warnings: {result.stderr[:200]})")
        
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
            progress_file = output_dir / "completed_tasks.jsonl"
            if progress_file.exists():
                progress_file.unlink()
                cleaned.append("output/completed_tasks.jsonl")
    
    return {
        "success": True,
        "mode": mode,
        "cleaned": cleaned
    }


@app.get("/logs")
async def get_logs(lines: int = Query(100, ge=10, le=1000)):
    """
    Get last N lines of system log.
    Distinguishes between file missing vs empty.
    """
    if not settings.SYSTEM_LOG.exists():
        return {"lines": [], "total": 0, "file_missing": True}
    
    try:
        content = settings.SYSTEM_LOG.read_text()
        if not content.strip():
            return {"lines": [], "total": 0, "file_missing": False}
        
        all_lines = content.strip().split("\n")
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        
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
        run_hours=request.run_hours,
        rest_hours=request.rest_hours
    )
    return {
        "success": True,
        "config": scheduler.get_config()
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
