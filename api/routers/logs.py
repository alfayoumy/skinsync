"""
Logs Router: Rotational file inventory, live log viewer, and on-demand log rotation.
"""

from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel

from pipeline.config import cfg
from pipeline.logging_config import get_log_files, read_recent_logs, rotate_logs, get_logger

logger = get_logger("skincare_engine.routers.logs")

router = APIRouter(tags=["logs"])

@router.get("/logs/files")
def list_log_files_endpoint():
    """Returns an inventory of all active and rotated log files, sizes, and timestamps."""
    files = get_log_files()
    return {
        "status": "success",
        "total": len(files),
        "log_directory": cfg.LOG_DIR,
        "files": files
    }

@router.get("/logs/recent")
def get_recent_logs_endpoint(
    file_name: Optional[str] = None,
    limit: int = 100,
    level: Optional[str] = None
):
    """
    Reads the most recent log entries from the primary or error rotational log file.
    Supports filtering by log level (INFO, WARNING, ERROR, DEBUG).
    """
    logs = read_recent_logs(file_name=file_name, lines=limit, level=level)
    return {
        "status": "success",
        "file_name": file_name or cfg.LOG_FILE_NAME,
        "total": len(logs),
        "limit": limit,
        "level_filter": level,
        "logs": logs
    }

@router.post("/logs/rotate")
def rotate_logs_endpoint():
    """Manually forces rotation of all active file handlers."""
    res = rotate_logs()
    logger.info("Manual log rotation triggered via API.")
    return res
