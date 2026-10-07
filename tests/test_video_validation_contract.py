import pytest
from fastapi import HTTPException

import core.video_validation as vv
from core.api.agnes_models import get_fallback_models


def _catalog(model="agnes-video-2.5-flash"):
    return {
        "models": {"text": [], "image": [], "video": [model]},
        "model_details": {
            model: {
                "type": "video",
                "capabilities": {"video": True},
            }
        },
        "source": "provider",
        "synced": True,
        "status_code": 200,
        "error": None,
    }


def test_sparse_provider_catalog_uses_documented_known_modes(monkeypatch):
    monkeypatch.setattr(vv, "fetch_model_catalog", lambda api_key: _catalog())
    result = vv.validate_video_request(
        api_key="configured",
        model="agnes-video-2.5-flash",
        mode="i2v",
        duration=5,
        has_reference=True,
        video_size="720P",
        aspect_ratio="16:9",
    )
    assert result["verified"] is True
    assert result["capabilities"]["i2v"] is True


def test_flash_rejects_non_720p(monkeypatch):
    monkeypatch.setattr(vv, "fetch_model_catalog", lambda api_key: _catalog())
    with pytest.raises(HTTPException, match="no admite la resolución"):
        vv.validate_video_request(
            api_key="configured",
            model="agnes-video-2.5-flash",
            mode="t2v",
            duration=5,
            video_size="1080P",
            aspect_ratio="16:9",
        )


def test_keyframe_requires_a_frame(monkeypatch):
    monkeypatch.setattr(vv, "fetch_model_catalog", lambda api_key: _catalog())
    with pytest.raises(HTTPException, match="keyframes requiere"):
        vv.validate_video_request(
            api_key="configured",
            model="agnes-video-2.5-flash",
            mode="keyframes",
            duration=5,
            video_size="720P",
            aspect_ratio="16:9",
        )


def test_1k_is_square(monkeypatch):
    model = "agnes-video-2.5"
    monkeypatch.setattr(vv, "fetch_model_catalog", lambda api_key: _catalog(model))
    with pytest.raises(HTTPException, match="1024x1024"):
        vv.validate_video_request(
            api_key="configured",
            model=model,
            mode="t2v",
            duration=5,
            video_size="1K",
            aspect_ratio="16:9",
        )


def test_production_rejects_unsynced_catalog(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(
        vv,
        "fetch_model_catalog",
        lambda api_key: {
            "models": get_fallback_models(),
            "model_details": {},
            "source": "fallback",
            "synced": False,
            "error": "provider unavailable",
            "status_code": None,
        },
    )
    with pytest.raises(HTTPException, match="No se puede validar"):
        vv.validate_video_request(
            api_key="configured",
            model="agnes-video-2.5-flash",
            mode="t2v",
            duration=5,
            video_size="720P",
            aspect_ratio="16:9",
        )
