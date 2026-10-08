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
    """Resolve capabilities conservatively.

    Provider metadata is authoritative when a field is explicitly present.
    For known Agnes models, documented local capability metadata fills only
    fields the provider response omitted. This avoids turning a sparse
    /models response into a false "text-only" model while still honoring an
    explicit provider denial.
    """
    details = catalog.get("model_details") or {}
    detail = details.get(model) or {}
    provider_caps = detail.get("capabilities") or {}
    if not isinstance(provider_caps, dict):
        provider_caps = {}

    local = get_video_model_capabilities().get(model) or {}
    local_modes = {
        str(item.get("id")).strip().lower()
        for item in (local.get("modes") or [])
        if isinstance(item, dict)
    }

    provider_modes = _list_cap(
        provider_caps, "modes", "mode", "video_modes", "generation_modes"
    )
    provider_t2v, provider_i2v, provider_keyframes = _provider_mode_support(provider_caps)

    # A sparse provider capability object can contain only {"video": true}.
    # In that case the known Agnes model metadata supplies the missing modes.
    provider_has_t2v = any(
        key in provider_caps for key in ("text_to_video", "t2v", "text")
    ) or bool(provider_modes)
    provider_has_i2v = any(
        key in provider_caps for key in ("image_to_video", "i2v")
    ) or bool(provider_modes & {"i2v", "image", "reference", "image_to_video", "image-to-video"})
    provider_has_keyframes = any(
        key in provider_caps for key in ("keyframes", "keyframe", "first_last_frame", "first_last_frames")
    ) or bool(provider_modes & {"keyframe", "keyframes", "first_last_frame", "first-last-frame"})

    local_t2v = bool(local_modes & {"t2v", "text", "reference"})
    local_i2v = bool(local_modes & {"i2v", "reference"})
    local_keyframes = bool(local_modes & {"keyframes", "keyframe"})

    t2v = provider_t2v if provider_has_t2v else local_t2v
    i2v = provider_i2v if provider_has_i2v else local_i2v
    keyframes = provider_keyframes if provider_has_keyframes else local_keyframes

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

    resolution = local.get("resolution") or {}
    sizes = list(resolution.get("sizes") or []) if isinstance(resolution, dict) else []
    provider_sizes = provider_caps.get("sizes") or provider_caps.get("resolution_sizes")
    if provider_sizes:
        sizes = list(provider_sizes) if isinstance(provider_sizes, (list, tuple, set)) else [str(provider_sizes)]

    supports_negative = (
        provider_caps.get("supports_negative")
        if "supports_negative" in provider_caps
        else local.get("supports_negative")
    )

    return {
        "t2v": t2v,
        "i2v": i2v,
        "keyframes": keyframes,
        "durations": [int(v) for v in durations] if isinstance(durations, (list, tuple, set)) else [],
        "max_ref_images": max_refs,
        "max_ref_audio": provider_caps.get("max_ref_audio", local.get("max_ref_audio")),
        "max_ref_videos": provider_caps.get("max_ref_videos", local.get("max_ref_videos", 1 if local.get("supports_ref_video") else 0)),
        "sizes": [str(v) for v in sizes],
        "ratios": list(resolution.get("ratios") or []) if isinstance(resolution, dict) else [],
        "supports_negative": bool(supports_negative),
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
    video_size: str | None = None,
    aspect_ratio: str | None = None,
    has_negative_prompt: bool = False,
    media_pending: bool = False,
    reference_image_count: int = 0,
    reference_audio_count: int = 0,
    reference_video_count: int = 0,
) -> dict[str, Any]:
    """Validate a video request before a durable task is created."""
    model = (model or "").strip()
    mode = (mode or "").strip().lower()

    if not model:
        raise HTTPException(status_code=422, detail="No hay un modelo de video seleccionado.")

    if mode not in {"t2v", "text", "i2v", "reference", "keyframes"}:
        raise HTTPException(status_code=422, detail=f"Modo de video no soportado: {mode}.")

    if duration < 1:
        raise HTTPException(status_code=422, detail="La duración del video debe ser positiva.")

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
    if min(reference_image_count, reference_audio_count, reference_video_count) < 0:
        raise HTTPException(status_code=422, detail="Las cantidades de referencias no pueden ser negativas.")

    has_first_or_reference = bool(has_reference or reference_image_count)
    has_any_reference_media = bool(reference_image_count or reference_audio_count or reference_video_count)
    has_any_media = bool(has_any_reference_media or has_end_frame or has_reference)

    if mode in {"t2v", "text"}:
        if not caps["t2v"]:
            raise HTTPException(
                status_code=422,
                detail=f"No es posible generar esta escena con '{model}' porque no soporta text-to-video.",
            )
        if has_any_media:
            raise HTTPException(
                status_code=422,
                detail="El modo text-to-video no acepta imágenes de referencia ni frames.",
            )

    if mode in {"i2v", "reference"}:
        if not caps["i2v"]:
            raise HTTPException(
                status_code=422,
                detail=f"No es posible generar esta escena con '{model}' porque no soporta image-to-video/reference.",
            )
        if mode == "i2v":
            if not has_first_or_reference and not media_pending:
                raise HTTPException(
                    status_code=422,
                    detail="El modo image-to-video requiere al menos una imagen de referencia.",
                )
            if has_end_frame or reference_audio_count or reference_video_count:
                raise HTTPException(
                    status_code=422,
                    detail="Image-to-video solo admite imágenes de referencia; usa reference para audio/video.",
                )
        elif not has_any_reference_media and not media_pending:
            raise HTTPException(
                status_code=422,
                detail="El modo reference requiere al menos una imagen, audio o video de referencia.",
            )

    if mode == "keyframes":
        if not caps["keyframes"]:
            raise HTTPException(
                status_code=422,
                detail=f"No es posible generar esta escena con '{model}' porque no soporta keyframes.",
            )
        if not (has_end_frame or media_pending):
            raise HTTPException(
                status_code=422,
                detail="El modo keyframes requiere first_frame, last_frame o ambos.",
            )

    durations = caps["durations"]
    if durations and duration not in durations:
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' no admite {duration}s. Duraciones disponibles: {durations}.",
        )

    sizes = caps["sizes"]
    if video_size and sizes and video_size not in sizes:
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' no admite la resolución '{video_size}'. Opciones: {sizes}.",
        )

    ratios = caps["ratios"]
    if aspect_ratio and ratios and aspect_ratio not in ratios:
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' no admite el aspect ratio '{aspect_ratio}'. Opciones: {ratios}.",
        )

    if video_size == "1K" and aspect_ratio and aspect_ratio != "1:1":
        raise HTTPException(
            status_code=422,
            detail="Agnes Video 2.5 1K es una salida fija 1024x1024; selecciona aspect ratio 1:1.",
        )

    if has_negative_prompt and not caps["supports_negative"]:
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' no admite negative prompt.",
        )

    max_refs = caps["max_ref_images"]
    ref_count = reference_image_count if reference_image_count else (int(bool(has_reference)) if mode == "i2v" else 0)
    if max_refs is not None and ref_count > int(max_refs):
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' admite como máximo {max_refs} imagen(es) de referencia.",
        )

    max_audio = caps["max_ref_audio"]
    if max_audio is not None and reference_audio_count > int(max_audio):
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' admite como máximo {max_audio} referencia(s) de audio.",
        )

    max_videos = caps["max_ref_videos"]
    if max_videos is not None and reference_video_count > int(max_videos):
        raise HTTPException(
            status_code=422,
            detail=f"El modelo '{model}' admite como máximo {max_videos} referencia(s) de video.",
        )

    max_audio = caps.get("max_ref_audio")
    max_videos = caps.get("max_ref_videos", 1 if caps.get("supports_ref_video") else 0)
    if mode != "reference" and (reference_audio_count or reference_video_count):
        raise HTTPException(status_code=422, detail="Audio/video de referencia solo están disponibles en modo reference.")
    if max_audio is not None and reference_audio_count > int(max_audio):
        raise HTTPException(status_code=422, detail=f"El modelo '{model}' admite como máximo {max_audio} audio(s) de referencia.")
    if reference_video_count > int(max_videos or 0):
        raise HTTPException(status_code=422, detail=f"El modelo '{model}' no admite esa cantidad de video(s) de referencia.")

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
        "parameters": {
            "duration": duration,
            "video_size": video_size,
            "aspect_ratio": aspect_ratio,
            "has_negative_prompt": has_negative_prompt,
            "media_pending": media_pending,
            "reference_image_count": reference_image_count,
            "reference_audio_count": reference_audio_count,
            "reference_video_count": reference_video_count,
        },
    }
