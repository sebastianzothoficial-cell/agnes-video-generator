"""Durable Supabase persistence for Agnes tasks."""
from __future__ import annotations
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
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
    """Atomically claim a queued task; recover a stale claim after 20 minutes."""
    if not enabled():
        return True
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        rows = _request(
            "PATCH",
            f"{TABLE}?task_key=eq.{task_id}&status=in.(pending,queued)",
            payload={"status": "running", "worker_claimed_at": now_iso},
            prefer="return=representation",
        ) or []
        if rows:
            return True
        # The configured FastAPI function maxDuration is 300s. A 20-minute
        # stale threshold safely recovers a hard-crashed worker without
        # allowing normal 15-minute executions to be claimed twice.
        stale_before = (now - timedelta(minutes=20)).isoformat()
        rows = _request(
            "PATCH",
            f"{TABLE}?task_key=eq.{task_id}&status=eq.running&worker_claimed_at=lt.{stale_before}",
            payload={"status": "running", "worker_claimed_at": now_iso},
            prefer="return=representation",
        ) or []
        return bool(rows)
    except Exception:
        logger.warning("[Supabase] Task claim failed for %s", task_id, exc_info=True)
        return False
def release_task_claim(task_id: str) -> bool:
    """Return an unexpectedly failed claimed task to queued for Queue redelivery."""
    if not enabled():
        return True
    try:
        rows = _request(
            "PATCH",
            f"{TABLE}?task_key=eq.{task_id}&status=eq.running",
            payload={"status": "queued", "worker_claimed_at": None},
            prefer="return=representation",
        ) or []
        return bool(rows)
    except Exception:
        logger.warning("[Supabase] Task claim release failed for %s", task_id, exc_info=True)
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


def _manifest_key(field: str, path: str) -> str:
    """Encode an input field and workspace-relative destination path."""
    from core.config import get_working_dir
    workspace = os.path.realpath(get_working_dir())
    absolute = os.path.realpath(path)
    relative = os.path.relpath(absolute, workspace)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        raise ValueError("input path is outside the active workspace")
    return f"{field}|{relative}"


def _manifest_local_path(relative_path: str) -> str:
    """Resolve a durable input destination inside the current worker workspace."""
    from core.config import get_working_dir
    workspace = os.path.realpath(get_working_dir())
    candidate = os.path.realpath(os.path.join(workspace, relative_path))
    if candidate != workspace and not candidate.startswith(workspace + os.sep):
        raise ValueError("durable input destination escapes workspace")
    return candidate


def _restore_state_input_paths(state: Any, restored: dict[str, list[str]]) -> None:
    """Rewrite task input fields to paths in the current worker workspace."""
    for field, paths in restored.items():
        if "." in field:
            base, subkey = field.split(".", 1)
            container = getattr(state, base, None)
            if isinstance(container, dict):
                container[subkey] = paths
            continue
        current = getattr(state, field, None)
        if isinstance(current, list):
            setattr(state, field, paths)
        elif isinstance(current, str) and paths:
            setattr(state, field, paths[0])


def sync_input_files(state: Any) -> dict[str, str]:
    """Upload local inputs and store workspace-relative restore destinations."""
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
            manifest_key = _manifest_key(field, path)
            with open(path, "rb") as fh:
                response = requests.post(
                    f"{url}/storage/v1/object/agnes-artifacts/{object_path}",
                    headers={"apikey": key, "Authorization": f"Bearer {key}",
                             "Content-Type": "application/octet-stream", "x-upsert": "true"},
                    data=fh, timeout=60,
                )
            response.raise_for_status()
            manifest[manifest_key] = object_path
        except Exception:
            logger.warning("[Supabase] Input upload failed for %s", state.task_id, exc_info=True)
    return manifest


def input_manifest_complete(state: Any, manifest: dict[str, str] | None) -> bool:
    """Check that the durable manifest contains every expected input file."""
    expected: dict[str, int] = {}
    for field, _path in _iter_state_files(state):
        expected[field] = expected.get(field, 0) + 1
    if not expected:
        return True
    actual: dict[str, int] = {}
    for source in (manifest or {}):
        if "|" not in source:
            continue
        field = source.split("|", 1)[0]
        actual[field] = actual.get(field, 0) + 1
    return all(actual.get(field, 0) == count for field, count in expected.items())


def download_input_files(
    task_id: str,
    manifest: dict[str, str],
    state: Any | None = None,
) -> bool:
    """Restore durable inputs into the current worker workspace.

    New manifests contain relative destinations. Legacy manifests containing
    absolute paths are still accepted and mapped to the current workspace.
    """
    cfg = _config()
    if not cfg:
        return not bool(manifest)
    url, key = cfg
    ok = True
    restored: dict[str, list[str]] = {}
    for source, object_path in (manifest or {}).items():
        if "|" not in source:
            ok = False
            continue
        field, stored_path = source.split("|", 1)
        try:
            if os.path.isabs(stored_path):
                # Legacy manifest: preserve only the basename under uploads.
                relative_path = os.path.join("uploads", os.path.basename(stored_path))
            else:
                relative_path = stored_path
            local_path = _manifest_local_path(relative_path)
            response = requests.get(
                f"{url}/storage/v1/object/agnes-artifacts/{object_path}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=60,
            )
            response.raise_for_status()
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            with open(local_path, "wb") as fh:
                fh.write(response.content)
            if not os.path.isfile(local_path) or os.path.getsize(local_path) == 0:
                raise IOError("restored file is missing or empty")
            restored.setdefault(field, []).append(local_path)
        except Exception:
            ok = False
            logger.warning("[Supabase] Input restore failed for %s", task_id, exc_info=True)
    if ok and state is not None:
        _restore_state_input_paths(state, restored)
    return ok

