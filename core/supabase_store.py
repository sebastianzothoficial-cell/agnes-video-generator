"""Durable Supabase persistence for Agnes tasks."""
from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any
import requests

logger = logging.getLogger(__name__)
TABLE = "agnes_tasks"
DEFAULT_SUPABASE_URL = "https://vfvfrwiyfvsbqhltdtzz.supabase.co"
DEFAULT_SUPABASE_ANON_KEY = "sb_publishable_icnJzBwsKaGZa2aK4aKGVA_vB2-bAEB"

def _config() -> tuple[str, str] | None:
    url = (os.getenv("SUPABASE_URL") or DEFAULT_SUPABASE_URL).strip().rstrip("/")
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY")
           or os.getenv("SUPABASE_ANON_KEY") or DEFAULT_SUPABASE_ANON_KEY).strip()
    return (url, key) if url and key else None

def enabled() -> bool:
    return _config() is not None

def _headers(key: str, *, prefer: str | None = None) -> dict[str, str]:
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer: headers["Prefer"] = prefer
    return headers

def _request(method: str, path: str, *, payload: Any = None, timeout: float = 8.0,
             prefer: str | None = None) -> Any:
    cfg = _config()
    if not cfg: return None
    url, key = cfg
    response = requests.request(method, f"{url}/rest/v1/{path}",
                                headers=_headers(key, prefer=prefer), json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json() if response.content else None

def upsert_task(state: Any, *, dir_name: str = "", input_files: dict[str, str] | None = None) -> bool:
    if not enabled(): return False
    try:
        data = state.model_dump(mode="json")
        status = data.get("status")
        if hasattr(status, "value"): status = status.value
        progress = float(data.get("current_progress") or 0)
        row = {
            "id": _uuid_for_task(state.task_id), "task_key": state.task_id,
            "workspace_id": None, "project_id": None, "task_type": str(data.get("task_type", "")),
            "status": str(status or "pending"),
            "title": data.get("creative_name") or data.get("prompt") or data.get("idea") or "",
            "prompt": data.get("prompt") or data.get("idea") or "",
            "input_payload": data,
            "config": {"dir_name": dir_name, "video_width": data.get("video_width"),
                       "video_height": data.get("video_height"),
                       "input_files": input_files or {}},
            "progress": max(0, min(100, int(progress * 100) if progress <= 1 else int(progress))),
            "error_message": data.get("current_message") if str(status) == "failed" else None,
            "error_details": {"traceback": data.get("error_traceback", "")} if data.get("error_traceback") else None,
            "started_at": _iso_or_none(data.get("created_at")),
            "completed_at": _iso_or_none(data.get("updated_at")) if str(status) == "completed" else None,
        }
        _request("POST", f"{TABLE}?on_conflict=id", payload=[row],
                 prefer="resolution=merge-duplicates,return=minimal")
        return True
    except Exception:
        logger.warning("[Supabase] Task mirror failed for %s", getattr(state, "task_id", "?"), exc_info=True)
        return False

def upload_final_video(state: Any, *, dir_name: str = "") -> str | None:
    cfg = _config()
    if not cfg: return None
    path = str(getattr(state, "final_video_file", "") or "")
    if not path or not os.path.isfile(path): return None
    try:
        url, key = cfg
        object_path = f"{state.task_id}/final_video.mp4"
        with open(path, "rb") as fh:
            response = requests.post(f"{url}/storage/v1/object/agnes-artifacts/{object_path}",
                headers={"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "video/mp4", "x-upsert": "true"},
                data=fh, timeout=30)
        response.raise_for_status()
        public_url = f"{url}/storage/v1/object/public/agnes-artifacts/{object_path}"
        _request("POST", "agnes_artifacts?on_conflict=task_id,artifact_type",
                 payload=[{"task_id": _uuid_for_task(state.task_id), "artifact_type": "final_video",
                           "name": "final_video.mp4", "storage_path": object_path,
                           "public_url": public_url, "mime_type": "video/mp4",
                           "size_bytes": os.path.getsize(path), "metadata": {"dir_name": dir_name}}],
                 prefer="resolution=merge-duplicates,return=minimal")
        return public_url
    except Exception:
        logger.warning("[Supabase] Final video upload failed for %s", getattr(state, "task_id", "?"), exc_info=True)
        return None

def claim_task(task_id: str) -> bool:
    """Atomically claim a queued task so at-least-once delivery cannot run it twice."""
    if not enabled():
        return True
    try:
        now = datetime.now(timezone.utc).isoformat()
        rows = _request(
            "PATCH",
            f"{TABLE}?task_key=eq.{task_id}&status=in.(pending,queued)",
            payload={"status": "running", "worker_claimed_at": now},
            prefer="return=representation",
        ) or []
        return bool(rows)
    except Exception:
        logger.warning("[Supabase] Task claim failed for %s", task_id, exc_info=True)
        return False
def get_task(task_id: str) -> dict | None:
    if not enabled(): return None
    try:
        rows = _request("GET", f"{TABLE}?select=task_key,input_payload,config,status,updated_at&task_key=eq.{task_id}") or []
        return rows[0] if rows else None
    except Exception:
        logger.warning("[Supabase] Task load failed for %s", task_id, exc_info=True)
        return None

def list_tasks() -> list[dict]:
    if not enabled(): return []
    try:
        rows = _request("GET", f"{TABLE}?select=id,task_key,task_type,status,title,config,created_at,updated_at&order=created_at.desc") or []
        return [{"task_id": row.get("task_key") or _task_id_from_uuid(row["id"]),
                 "dir_name": (row.get("config") or {}).get("dir_name", ""),
                 "task_type": row.get("task_type", "creative"), "creative_name": row.get("title", ""),
                 "status": row.get("status", "pending"), "chaining_mode": ""} for row in rows]
    except Exception:
        logger.warning("[Supabase] Task listing failed", exc_info=True)
        return []

def _uuid_for_task(task_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agnes-task:{task_id}"))

def _task_id_from_uuid(value: str) -> str:
    return value

def _iso_or_none(value: Any) -> str | None:
    return value if value else None


def get_final_video_url(task_id: str) -> str | None:
    """Return the durable public URL for a completed final video, if recorded."""
    if not enabled():
        return None
    try:
        rows = _request(
            "GET",
            f"agnes_artifacts?select=public_url&task_id=eq.{_uuid_for_task(task_id)}&artifact_type=eq.final_video&limit=1",
        ) or []
        return rows[0].get("public_url") if rows else None
    except Exception:
        logger.warning("[Supabase] Artifact URL lookup failed for %s", task_id, exc_info=True)
        return None


_FILE_FIELDS = (
    "reference_image", "end_frame_image", "end_frame_images",
    "scene_reference_images", "reference_images", "anchor_reference_image",
)

def _iter_state_files(state: Any):
    for field in _FILE_FIELDS:
        value = getattr(state, field, None)
        if isinstance(value, str) and value:
            yield field, value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item:
                    yield field, item
        elif isinstance(value, dict):
            for key, items in value.items():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, str) and item:
                            yield f"{field}.{key}", item

def sync_input_files(state: Any) -> dict[str, str]:
    """Upload local input files so a later worker can restore them."""
    cfg = _config()
    if not cfg:
        return {}
    url, key = cfg
    manifest: dict[str, str] = {}
    for field, path in _iter_state_files(state):
        if not os.path.isfile(path):
            continue
        name = os.path.basename(path)
        object_path = f"{state.task_id}/inputs/{uuid.uuid4().hex}_{name}"
        try:
            with open(path, "rb") as fh:
                response = requests.post(
                    f"{url}/storage/v1/object/agnes-artifacts/{object_path}",
                    headers={"apikey": key, "Authorization": f"Bearer {key}",
                             "Content-Type": "application/octet-stream", "x-upsert": "true"},
                    data=fh, timeout=60,
                )
            response.raise_for_status()
            manifest[f"{field}|{path}"] = object_path
        except Exception:
            logger.warning("[Supabase] Input upload failed for %s (%s)", state.task_id, path, exc_info=True)
    return manifest

def download_input_files(task_id: str, manifest: dict[str, str]) -> None:
    """Restore durable input files into their original local paths."""
    cfg = _config()
    if not cfg:
        return
    url, key = cfg
    for source, object_path in (manifest or {}).items():
        if "|" not in source:
            continue
        _, local_path = source.split("|", 1)
        try:
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            response = requests.get(
                f"{url}/storage/v1/object/agnes-artifacts/{object_path}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=60,
            )
            response.raise_for_status()
            with open(local_path, "wb") as fh:
                fh.write(response.content)
        except Exception:
            logger.warning("[Supabase] Input restore failed for %s -> %s", task_id, local_path, exc_info=True)


def input_manifest_complete(state: Any, manifest: dict[str, str] | None) -> bool:
    expected = list(_iter_state_files(state))
    if not expected:
        return True
    keys = set((manifest or {}).keys())
    return all(f"{field}|{path}" in keys for field, path in expected)
