import pytest

import core.api.agnes_video as module
from core.api.agnes_video import AgnesVideoAPI


@pytest.mark.asyncio
async def test_v25_keyframe_payload_uses_first_and_last_frames(monkeypatch):
    api = AgnesVideoAPI(api_key="configured", model="agnes-video-2.5-flash")
    captured = {}

    async def fake_resolve(path):
        return f"https://public.example/{path}"

    async def fake_submit(payload, mode, progress_callback=None):
        captured["payload"] = payload
        captured["mode"] = mode
        return "video_test"

    monkeypatch.setattr(api, "_resolve_image_ref", fake_resolve)
    monkeypatch.setattr(api, "_submit_with_retry", fake_submit)
    monkeypatch.setattr(module, "normalize_reference_path", lambda path, width, height: path)

    video_id = await api.submit_video(
        prompt="single coherent shot",
        generation_mode="keyframe",
        first_frame_path="first.png",
        last_frame_path="last.png",
        duration=5,
        width=1280,
        height=720,
        video_size="720P",
    )

    assert video_id == "video_test"
    assert captured["mode"] == "keyframe"
    payload = captured["payload"]
    assert payload["model"] == "agnes-video-2.5-flash"
    assert payload["mode"] == "keyframe"
    assert payload["seconds"] == "5"
    assert payload["size"] == "720P"
    assert payload["n"] == 1
    assert payload["first_frame"].endswith("/first.png")
    assert payload["last_frame"].endswith("/last.png")
    assert "images" not in payload


@pytest.mark.asyncio
async def test_flash_forces_720p(monkeypatch):
    api = AgnesVideoAPI(api_key="configured", model="agnes-video-2.5-flash")
    captured = {}

    async def fake_submit(payload, mode, progress_callback=None):
        captured["payload"] = payload
        return "video_test"

    monkeypatch.setattr(api, "_submit_with_retry", fake_submit)

    await api.submit_video(
        prompt="test",
        duration=5,
        width=1280,
        height=720,
        video_size="2K",
    )

    assert captured["payload"]["size"] == "720P"
