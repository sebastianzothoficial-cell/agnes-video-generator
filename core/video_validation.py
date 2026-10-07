"""Pre-flight validation for Agnes video task creation.

This module is deliberately provider-first: a task is accepted only when the
selected video model was confirmed by Agnes' current catalog. Local capability
metadata is used only for models that Agnes actually returned.
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


def validate_video_request(
    *,
    api_key: str,
    model: str,
    mode: str,
    duration: int,
    has_reference: bool = False,
    has_end_frame: bool = False,
) -> dict[str, Any]:
    """Validate a video request before a durable task is created.

    In local development the check is opt-in unless Vercel is detected, which
    preserves offline/unit-test workflows. Production is strict by default.
    """
    model = (model or "").strip()
    mode = (mode or "").strip().lower()
    if not model:
        raise HTTPException(status_code=422, detail="No hay un modelo de video seleccionado.")

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

    caps = get_video_model_capabilities().get(model) or {}
    modes = {str(item.get("id")) for item in (caps.get("modes") or []) if isinstance(item, dict)}

    required_mode = mode
    if required_mode == "i2v":
        acceptable = {"i2v", "reference", "keyframe"}
        if not (modes & acceptable):
            raise HTTPException(
                status_code=422,
                detail=f"No es posible generar esta escena con '{model}' porque no soporta image-to-video.",
            )
    elif required_mode == "keyframes":
        if not (modes & {"keyframes", "keyframe"}):
            raise HTTPException(
                status_code=422,
                detail=f"No es posible generar esta escena con '{model}' porque no soporta keyframes.",
            )
    elif required_mode in {"t2v", "text"}:
        if modes and not (modes & {"t2v", "text"}):
            raise HTTPException(
                status_code=422,
                detail=f"No es posible generar esta escena con '{model}' porque no soporta text-to-video.",
            )
    else:
        raise HTTPException(status_code=422, detail=f"Modo de video no soportado: {mode}.")

    durations = caps.get("durations") or []
    if durations and duration not in durations:
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' no admite {duration}s. Duraciones disponibles: {durations}.",
        )

    max_refs = caps.get("max_ref_images")
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
    }
