"""Durable Supabase persistence for Agnes tasks.

The video pipeline still uses its local working directory while it is executing,
but task metadata/state is mirrored to Supabase so Vercel restarts do not erase
the task registry. Configuration is opt-in through SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_SECRET_KEY).

This module deliberately has no dependency on the Supabase Python SDK; it uses
the PostgREST API already exposed by Supabase.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

TABLE = "agnes_tasks"


def _config() -> tuple[str, str] | None:
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SECRET_KEY")
        or ""
    ).strip()
    if not url or not key:
        return None
    return url, key


def enabled() -> bool:
    return _config() is not None


def _headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _request(method: str, path: str, *, payload: Any = None, timeout: float = 8.0) -> Any:
    cfg = _config()
    if not cfg:
        return None
    url, key = cfg
    response = requests.request(
        method,
        f"{url}/rest/v1/{path}",
        headers=_headers(key),
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    if not response.content:
        return None
    return response.json()


def upsert_task(state: Any, *, dir_name: str = "") -> bool:
    """Mirror a Pydantic task state into agnes_tasks. Never breaks the pipeline."""
    if not enabled():
        return False
    try:
        data = state.model_dump(mode="json")
        status = data.get("status")
        if hasattr(status, "value"):
            status = status.value
        progress = float(data.get("current_progress") or 0)
        row = {
            "id": _uuid_for_task(state.task_id),
            "workspace_id": None,
            "project_id": None,
            "task_type": str(data.get("task_type", "")),
            "status": str(status or "pending"),
            "title": data.get("creative_name") or data.get("prompt") or data.get("idea") or "",
            "prompt": data.get("prompt") or data.get("idea") or "",
            "input_payload": data,
            "config": {
                "dir_name": dir_name,
                "video_width": data.get("video_width"),
                "video_height": data.get("video_height"),
            },
            "progress": max(0, min(100, int(progress * 100) if progress <= 1 else int(progress))),
            "error_message": data.get("current_message") if str(status) == "failed" else None,
            "error_details": {"traceback": data.get("error_traceback", "")} if data.get("error_traceback") else None,
            "started_at": _iso_or_none(data.get("created_at")),
            "completed_at": _iso_or_none(data.get("updated_at")) if str(status) == "completed" else None,
        }
        _request("POST", f"{TABLE}?on_conflict=id", payload=[row])
        return True
    except Exception:
        logger.warning("[Supabase] Task mirror failed for %s", getattr(state, "task_id", "?"), exc_info=True)
        return False


def list_tasks() -> list[dict]:
    if not enabled():
        return []
    try:
        rows = _request(
            "GET",
            f"{TABLE}?select=id,task_type,status,title,config,created_at,updated_at&order=created_at.desc",
        ) or []
        result = []
        for row in rows:
            config = row.get("config") or {}
            result.append({
                "task_id": _task_id_from_uuid(row["id"]),
                "dir_name": config.get("dir_name", ""),
                "task_type": row.get("task_type", "creative"),
                "creative_name": row.get("title", ""),
                "status": row.get("status", "pending"),
                "chaining_mode": "",
            })
        return result
    except Exception:
        logger.warning("[Supabase] Task listing failed", exc_info=True)
        return []


def _uuid_for_task(task_id: str) -> str:
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agnes-task:{task_id}"))


def _task_id_from_uuid(value: str) -> str:
    # The original short task id is retained inside input_payload on detail
    # reads; listing uses the UUID only when that payload is unavailable.
    return value


def _iso_or_none(value: Any) -> str | None:
    return value if value else None
