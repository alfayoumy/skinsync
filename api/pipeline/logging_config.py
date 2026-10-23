"""
Centralized Rotational Logging Architecture for SkinSync.
Provides thread-safe and process-safe size/time-based rotating file logging,
dedicated error log rotation, task context injection, structured JSON formatting,
and runtime rotation controls.
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone

from pipeline.config import cfg

# Thread-safe context variables for active task tracking
_current_task_id: ContextVar[Optional[str]] = ContextVar("current_task_id", default=None)
_current_subtask_id: ContextVar[Optional[str]] = ContextVar("current_subtask_id", default=None)

# Global registry of active rotating handlers for runtime management
_active_handlers: Dict[str, logging.Handler] = {}
_logging_initialized: bool = False

# ==============================================================================
# 1. TASK CONTEXT FILTER & FORMATTERS
# ==============================================================================

class TaskContextFilter(logging.Filter):
    """
    Injects task_id and subtask_id context variables into every LogRecord,
    providing a formatted %(task_prefix)s for console and file output.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        task_id = getattr(record, "task_id", None) or _current_task_id.get()
        subtask_id = getattr(record, "subtask_id", None) or _current_subtask_id.get()

        record.task_id = task_id
        record.subtask_id = subtask_id

        if task_id and subtask_id:
            record.task_prefix = f" [{task_id}:{subtask_id}]"
        elif task_id:
            record.task_prefix = f" [{task_id}]"
        else:
            record.task_prefix = ""

        return True

class JsonLogFormatter(logging.Formatter):
    """
    Outputs structured JSON log records for ingestion into log aggregators (ELK, Datadog, CloudWatch).
    """
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "task_id": getattr(record, "task_id", None),
            "subtask_id": getattr(record, "subtask_id", None),
            "process": record.process,
            "thread": record.threadName,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False)

# ==============================================================================
# 2. CONTEXT MANAGERS FOR TASK TRACING
# ==============================================================================

def set_task_context(task_id: Optional[str], subtask_id: Optional[str] = None):
    """Sets the active task context for the current async execution flow or thread."""
    _current_task_id.set(task_id)
    _current_subtask_id.set(subtask_id)

def clear_task_context():
    """Clears the active task context."""
    _current_task_id.set(None)
    _current_subtask_id.set(None)

@contextmanager
def log_context(task_id: Optional[str], subtask_id: Optional[str] = None):
    """Context manager for tracing log records with a task ID and subtask ID."""
    token_task = _current_task_id.set(task_id)
    token_sub = _current_subtask_id.set(subtask_id)
    try:
        yield
    finally:
        _current_task_id.reset(token_task)
        _current_subtask_id.reset(token_sub)

# ==============================================================================
# 3. ROTATIONAL LOGGING SETUP
# ==============================================================================

def setup_logging(
    log_level: Optional[str] = None,
    log_dir: Optional[str] = None,
    log_file_name: Optional[str] = None,
    log_error_file_name: Optional[str] = None,
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
    rotation_type: Optional[str] = None,
    to_console: Optional[bool] = None,
    to_file: Optional[bool] = None,
    json_format: Optional[bool] = None,
    force_reconfigure: bool = False
) -> Dict[str, logging.Handler]:
    """
    Initializes and configures centralized rotational logging.
    Idempotent: will not duplicate handlers unless force_reconfigure is True.
    """
    global _logging_initialized, _active_handlers

    if _logging_initialized and not force_reconfigure:
        return _active_handlers

    # Resolve settings from parameters or centralized config
    level_str = (log_level or cfg.LOG_LEVEL).upper().strip()
    numeric_level = getattr(logging, level_str, logging.INFO)
    target_dir = log_dir or cfg.LOG_DIR
    main_file = log_file_name or cfg.LOG_FILE_NAME
    error_file = log_error_file_name or cfg.LOG_ERROR_FILE_NAME
    max_b = max_bytes if max_bytes is not None else cfg.LOG_MAX_BYTES
    backups = backup_count if backup_count is not None else cfg.LOG_BACKUP_COUNT
    rot_type = (rotation_type or cfg.LOG_ROTATION_TYPE).lower().strip()
    enable_console = to_console if to_console is not None else cfg.LOG_TO_CONSOLE
    enable_file = to_file if to_file is not None else cfg.LOG_TO_FILE
    use_json = json_format if json_format is not None else cfg.LOG_JSON_FORMAT

    # Root and project loggers
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Detach previous handlers managed by this module to prevent duplicate logs
    for h in list(_active_handlers.values()):
        try:
            root_logger.removeHandler(h)
            h.close()
        except Exception:
            pass
    _active_handlers.clear()

    # Formatter setup
    context_filter = TaskContextFilter()
    if use_json:
        formatter = JsonLogFormatter()
    else:
        fmt_str = "%(asctime)s [%(levelname)-7s] [%(name)s]%(task_prefix)s %(message)s"
        formatter = logging.Formatter(fmt_str, datefmt="%Y-%m-%d %H:%M:%S")

    # 1. Console Handler
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(context_filter)
        root_logger.addHandler(console_handler)
        _active_handlers["console"] = console_handler

    # 2. File Handlers (Rotational)
    if enable_file:
        os.makedirs(target_dir, exist_ok=True)
        main_path = os.path.join(target_dir, main_file)
        error_path = os.path.join(target_dir, error_file)

        # Main App Rotational Handler
        if rot_type == "time":
            main_handler = TimedRotatingFileHandler(
                main_path,
                when=cfg.LOG_TIME_INTERVAL,
                interval=1,
                backupCount=cfg.LOG_TIME_BACKUP_DAYS,
                encoding="utf-8"
            )
        else:
            main_handler = RotatingFileHandler(
                main_path,
                maxBytes=max_b,
                backupCount=backups,
                encoding="utf-8"
            )
        main_handler.setLevel(numeric_level)
        main_handler.setFormatter(formatter)
        main_handler.addFilter(context_filter)
        root_logger.addHandler(main_handler)
        _active_handlers["file_main"] = main_handler

        # Dedicated Error Rotational Handler (Captures only ERROR and CRITICAL)
        error_handler = RotatingFileHandler(
            error_path,
            maxBytes=max_b,
            backupCount=backups,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        error_handler.addFilter(context_filter)
        root_logger.addHandler(error_handler)
        _active_handlers["file_error"] = error_handler

    # Silence overly verbose third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("passlib").setLevel(logging.WARNING)

    _logging_initialized = True
    root_logger.info(
        f"Centralized rotational logging initialized (Level: {level_str}, Dir: {target_dir}, Type: {rot_type}, Handlers: {list(_active_handlers.keys())})"
    )
    return _active_handlers

def get_logger(name: str) -> logging.Logger:
    """
    Returns a configured logger instance under the centralized logging hierarchy.
    Ensures rotational logging handlers are initialized.
    """
    if not _logging_initialized:
        setup_logging()
    return logging.getLogger(name)

# ==============================================================================
# 4. RUNTIME LOG ROTATION & AUDIT UTILITIES
# ==============================================================================

def rotate_logs() -> Dict[str, Any]:
    """
    Manually forces rotation of all active rotating file handlers.
    Useful for maintenance, cron jobs, and on-demand archiving.
    """
    rotated = []
    for name, handler in _active_handlers.items():
        if isinstance(handler, (RotatingFileHandler, TimedRotatingFileHandler)):
            try:
                handler.doRollover()
                rotated.append(name)
            except Exception as e:
                logging.getLogger("skincare_pipeline.logging").error(f"Failed to rotate {name}: {e}")
    return {
        "status": "success",
        "rotated_handlers": rotated,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

def get_log_files(log_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns an inventory of all existing log files, archives, sizes, and timestamps.
    """
    target_dir = log_dir or cfg.LOG_DIR
    if not os.path.exists(target_dir):
        return []

    files = []
    for f in sorted(os.listdir(target_dir)):
        if f.endswith(".log") or ".log." in f:
            p = os.path.join(target_dir, f)
            try:
                st = os.stat(p)
                files.append({
                    "file_name": f,
                    "path": p,
                    "size_bytes": st.st_size,
                    "size_kb": round(st.st_size / 1024, 2),
                    "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                    "is_current": f in (cfg.LOG_FILE_NAME, cfg.LOG_ERROR_FILE_NAME)
                })
            except Exception:
                pass
    return sorted(files, key=lambda x: x["modified_at"], reverse=True)

def read_recent_logs(
    file_name: Optional[str] = None,
    lines: int = 100,
    level: Optional[str] = None,
    log_dir: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Reads and parses the most recent log records from a specified log file.
    """
    target_dir = log_dir or cfg.LOG_DIR
    target_file = file_name or cfg.LOG_FILE_NAME
    full_path = os.path.join(target_dir, target_file)

    if not os.path.exists(full_path):
        return []

    max_lines = max(1, min(lines, 1000))
    target_level = level.upper().strip() if level else None

    # Read trailing lines efficiently
    raw_lines = []
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()
    except Exception as e:
        return [{"error": f"Failed to read log file: {e}"}]

    parsed = []
    for line in reversed(raw_lines):
        line_clean = line.strip()
        if not line_clean:
            continue

        # Try parsing JSON format
        if line_clean.startswith("{") and line_clean.endswith("}"):
            try:
                data = json.loads(line_clean)
                if target_level and data.get("level") != target_level:
                    continue
                parsed.append(data)
                if len(parsed) >= max_lines:
                    break
                continue
            except json.JSONDecodeError:
                pass

        # Standard formatted line fallback
        # e.g. "2026-10-23 21:00:00 [INFO   ] [skincare_engine] [task_123] Message here"
        parts = line_clean.split(" ", 4)
        record = {
            "raw": line_clean,
            "timestamp": f"{parts[0]} {parts[1]}" if len(parts) >= 2 else None,
            "level": parts[2].strip("[] ") if len(parts) >= 3 else "INFO",
            "message": line_clean
        }
        if target_level and record["level"] != target_level:
            continue
        parsed.append(record)
        if len(parsed) >= max_lines:
            break

    return parsed
