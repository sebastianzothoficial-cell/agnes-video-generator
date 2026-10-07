"""Pre-flight validation for Agnes video task creation.

Provider catalog is authoritative. Provider capability metadata is used first;
local metadata is only a compatibility fallback for a model that Agnes has
actually returned in its current catalog.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from core.api.agnes_models import fetch_model_catalog
from core.config import get_video_model_capabilities


def _strict_enabled() -> bool:
    value = os.getenv("AGNES_VALIDATE_MODEL_CATALOG", "")
    if value:
        return value.strip().lower() not in {"0", "false", "off"}
    return bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))


def _bool_cap(caps: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = caps.get(key)
        if value is True:
            return True
        if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
            return True
    return False


def _list_cap(caps: dict[str, Any], *keys: str) -> set[str]:
    values: set[str] = set()
    for key in keys:
        raw = caps.get(key)
        if isinstance(raw, str):
            values.add(raw.strip().lower())
        elif isinstance(raw, (list, tuple, set)):
            values.update(str(item).strip().lower() for item in raw)
    return {v for v in values if v}


def _provider_mode_support(caps: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Return (t2v, i2v, keyframes) from provider metadata."""
    modes = _list_cap(caps, "modes", "mode", "video_modes", "generation_modes")
    t2v = _bool_cap(caps, "text_to_video", "t2v", "text")
    i2v = _bool_cap(caps, "image_to_video", "i2v", "image_to_video")
    keyframes = _bool_cap(caps, "keyframes", "keyframe", "first_last_frame", "first_last_frames")

    for mode in modes:
        if mode in {"t2v", "text", "text_to_video", "text-to-video"}:
            t2v = True
        elif mode in {"i2v", "image", "reference", "image_to_video", "image-to-video"}:
            i2v = True
        elif mode in {"keyframe", "keyframes", "first_last_frame", "first-last-frame"}:
            keyframes = True

    # If the provider explicitly marks video generation but exposes no mode
    # detail, text-to-video is the least-assumptive baseline.
    if _bool_cap(caps, "video", "video_generation") and not (t2v or i2v or keyframes):
        t2v = True
    return t2v, i2v, keyframes


def _effective_capabilities(catalog: dict[str, Any], model: str) -> dict[str, Any]:
    details = catalog.get("model_details") or {}
    detail = details.get(model) or {}
    provider_caps = detail.get("capabilities") or {}
    if not isinstance(provider_caps, dict):
        provider_caps = {}

    # Agnes metadata wins whenever it actually describes a capability.
    t2v, i2v, keyframes = _provider_mode_support(provider_caps)
    explicit_provider = bool(provider_caps)

    local = get_video_model_capabilities().get(model) or {}
    local_modes = {
        str(item.get("id")).strip().lower()
        for item in (local.get("modes") or [])
        if isinstance(item, dict)
    }

    if not explicit_provider:
        t2v = bool(local_modes & {"t2v", "text"}) or t2v
        i2v = bool(local_modes & {"i2v", "reference"}) or i2v
        keyframes = bool(local_modes & {"keyframes", "keyframe"}) or keyframes

    durations = provider_caps.get("durations") or provider_caps.get("duration_options")
    if not durations:
        durations = local.get("durations") or []

    max_refs = (
        provider_caps.get("max_ref_images")
        if "max_ref_images" in provider_caps
        else provider_caps.get("max_reference_images")
    )
    if max_refs is None:
        max_refs = local.get("max_ref_images")

    return {
        "t2v": t2v,
        "i2v": i2v,
        "keyframes": keyframes,
        "durations": list(durations) if isinstance(durations, (list, tuple, set)) else [],
        "max_ref_images": max_refs,
        "provider_capabilities": provider_caps,
    }


def validate_video_request(
    *,
    api_key: str,
    model: str,
    mode: str,
    duration: int,
    has_reference: bool = False,
    has_end_frame: bool = False,
) -> dict[str, Any]:
    """Validate a video request before a durable task is created."""
    model = (model or "").strip()
    mode = (mode or "").strip().lower()

    if not model:
        raise HTTPException(status_code=422, detail="No hay un modelo de video seleccionado.")

    if mode not in {"t2v", "text", "i2v", "keyframes"}:
        raise HTTPException(status_code=422, detail=f"Modo de video no soportado: {mode}.")

    if not _strict_enabled():
        return {"verified": False, "source": "local", "model": model, "mode": mode}

    catalog = fetch_model_catalog(api_key)
    if catalog.get("source") != "provider" or not catalog.get("synced"):
        reason = catalog.get("error") or "No se pudo sincronizar el catálogo de Agnes."
        raise HTTPException(
            status_code=503,
            detail=f"No se puede validar el modelo de video con Agnes: {reason}",
        )

    available = set(catalog.get("models", {}).get("video", []))
    if model not in available:
        raise HTTPException(
            status_code=422,
            detail=f"El modelo de video '{model}' no está disponible para esta API key según Agnes.",
        )

    caps = _effective_capabilities(catalog, model)
    if mode in {"t2v", "text"} and not caps["t2v"]:
        raise HTTPException(
            status_code=422,
            detail=f"No es posible generar esta escena con '{model}' porque no soporta text-to-video.",
        )
    if mode == "i2v" and not caps["i2v"]:
        raise HTTPException(
            status_code=422,
            detail=f"No es posible generar esta escena con '{model}' porque no soporta image-to-video.",
        )
    if mode == "keyframes" and not caps["keyframes"]:
        raise HTTPException(
            status_code=422,
            detail=f"No es posible generar esta escena con '{model}' porque no soporta keyframes.",
        )

    durations = caps["durations"]
    if durations and duration not in durations:
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' no admite {duration}s. Duraciones disponibles: {durations}.",
        )

    max_refs = caps["max_ref_images"]
    ref_count = int(bool(has_reference)) + int(bool(has_end_frame))
    if max_refs is not None and ref_count > int(max_refs):
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' admite como máximo {max_refs} imagen(es) de referencia.",
        )

    return {
        "verified": True,
        "source": "provider",
        "model": model,
        "mode": mode,
        "catalog_status": catalog.get("status_code"),
        "capabilities": {
            "t2v": caps["t2v"],
            "i2v": caps["i2v"],
            "keyframes": caps["keyframes"],
        },
    }
