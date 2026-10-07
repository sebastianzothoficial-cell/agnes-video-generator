"""Agnes model catalog.

The provider catalog is authoritative whenever it can be queried successfully.
Fallback data is kept only for UI continuity and is explicitly marked as
unverified by the route layer.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from core.config import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VIDEO_MODEL,
    get_base_url_for_key,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20

_FALLBACK = {
    "text": [DEFAULT_TEXT_MODEL],
    "image": [DEFAULT_IMAGE_MODEL],
    "video": [DEFAULT_VIDEO_MODEL],
}

_DEPRECATED_MODELS = {"agnes-2.0-flash"}


def _classify(model: dict[str, Any]) -> str | None:
    """Classify only when the provider gives enough evidence.

    Agnes currently exposes model ids consistently enough for the built-in
    families, but metadata wins when present. Unknown models are not guessed
    into a video/image bucket: they remain unclassified and therefore cannot
    be selected for a capability-sensitive task.
    """
    model_id = str(model.get("id") or "").strip()
    if not model_id or model_id in _DEPRECATED_MODELS:
        return None

    raw_type = str(
        model.get("type")
        or model.get("kind")
        or model.get("model_type")
        or ""
    ).lower()
    if raw_type in {"video", "video_generation"}:
        return "video"
    if raw_type in {"image", "image_generation"}:
        return "image"
    if raw_type in {"text", "chat", "language"}:
        return "text"

    capabilities = model.get("capabilities") or {}
    if isinstance(capabilities, dict):
        if capabilities.get("video") or capabilities.get("text_to_video"):
            return "video"
        if capabilities.get("image") or capabilities.get("image_generation"):
            return "image"
        if capabilities.get("text") or capabilities.get("chat"):
            return "text"

    # Provider model ids are the remaining stable discriminator used by the
    # Agnes API family. This does not invent unknown capabilities.
    if model_id.startswith("agnes-video"):
        return "video"
    if model_id.startswith("agnes-image"):
        return "image"
    if model_id.startswith("agnes-"):
        return "text"
    return None


def get_fallback_models() -> dict[str, list[str]]:
    """Return unverified continuity data; never treat it as provider truth."""
    return {key: list(models) for key, models in _FALLBACK.items()}


def _extract_capabilities(model: dict[str, Any], kind: str | None) -> dict[str, Any]:
    raw = model.get("capabilities")
    if isinstance(raw, dict):
        return dict(raw)

    # Some OpenAI-compatible model registries expose input/output modalities.
    inputs = model.get("input_modalities") or model.get("modalities") or []
    outputs = model.get("output_modalities") or []
    if isinstance(inputs, str):
        inputs = [inputs]
    if isinstance(outputs, str):
        outputs = [outputs]

    caps: dict[str, Any] = {}
    values = {str(v).lower() for v in [*inputs, *outputs]}
    if kind == "video":
        caps["video"] = True
        caps["text_to_video"] = "text" in values or not values
        caps["image_to_video"] = "image" in values
    elif kind == "image":
        caps["image"] = True
    elif kind == "text":
        caps["text"] = True
    return caps


def fetch_model_catalog(api_key: str) -> dict[str, Any]:
    """Fetch the authoritative Agnes catalog.

    Returns:
      models: grouped ids
      model_details: per-id provider metadata/capabilities
      source: provider | fallback
      synced: whether the provider call succeeded
      error: safe provider failure description, if any
      status_code: HTTP status when available
    """
    if not api_key:
        return {
            "models": get_fallback_models(),
            "model_details": {},
            "source": "fallback",
            "synced": False,
            "error": "AGNES_API_KEY is not configured",
            "status_code": None,
        }

    endpoint = f"{get_base_url_for_key(api_key)}/models?all=true"
    try:
        resp = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            # Do not expose response bodies: provider errors can contain
            # request metadata and must never end up in logs/UI.
            logger.warning(
                "[AgnesModels] catalog request failed status=%s endpoint=%s",
                resp.status_code,
                endpoint.split("/v1/")[0] + "/v1/models",
            )
            return {
                "models": get_fallback_models(),
                "model_details": {},
                "source": "fallback",
                "synced": False,
                "error": f"Agnes model catalog returned HTTP {resp.status_code}",
                "status_code": resp.status_code,
            }

        data = resp.json()
        raw_models = data.get("data", [])
        if not isinstance(raw_models, list):
            raise ValueError("Agnes model catalog response has invalid data")

        grouped = {"text": [], "image": [], "video": []}
        details: dict[str, dict[str, Any]] = {}
        unclassified: list[str] = []

        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            kind = _classify(item)
            if not model_id or kind is None:
                if model_id and model_id not in _DEPRECATED_MODELS:
                    unclassified.append(model_id)
                continue
            grouped[kind].append(model_id)
            details[model_id] = {
                "type": kind,
                "provider": dict(item),
                "capabilities": _extract_capabilities(item, kind),
            }

        # A successful provider response is authoritative, including empty
        # groups. Never inject local defaults into a successful catalog.
        return {
            "models": grouped,
            "model_details": details,
            "unclassified": sorted(set(unclassified)),
            "source": "provider",
            "synced": True,
            "error": None,
            "status_code": 200,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[AgnesModels] catalog fetch failed endpoint=%s error=%s",
            endpoint.split("/v1/")[0] + "/v1/models",
            exc,
        )
        return {
            "models": get_fallback_models(),
            "model_details": {},
            "source": "fallback",
            "synced": False,
            "error": f"Agnes model catalog unavailable: {type(exc).__name__}",
            "status_code": None,
        }


def fetch_available_models(api_key: str) -> dict[str, list[str]]:
    """Backward-compatible grouped catalog accessor."""
    return fetch_model_catalog(api_key)["models"]
