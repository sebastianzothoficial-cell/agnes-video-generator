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


@pytest.mark.asyncio
async def test_v25_reference_payload_supports_images_audio_and_video(monkeypatch):
    api = AgnesVideoAPI(api_key="configured", model="agnes-video-2.5")
    captured = {}

    async def fake_resolve(path):
        return f"https://public.example/{path}"

    async def fake_submit(payload, mode, progress_callback=None):
        captured["payload"] = payload
        return "video_test"

    monkeypatch.setattr(api, "_resolve_image_ref", fake_resolve)
    monkeypatch.setattr(api, "_submit_with_retry", fake_submit)
    monkeypatch.setattr(module, "normalize_reference_path", lambda path, width, height: path)

    await api.submit_video(
        prompt="reference composition",
        reference_image_paths=[f"image-{i}.png" for i in range(8)],
        reference_audio_paths=[
            "https://public.example/a1.mp3",
            "https://public.example/a2.mp3",
            "https://public.example/a3.mp3",
        ],
        reference_video_path="https://public.example/ref.mp4",
        generation_mode="reference",
        duration=5,
        width=1280,
        height=720,
        video_size="1080P",
    )

    payload = captured["payload"]
    assert len(payload["images"]) == 8
    assert len(payload["audios"]) == 3
    assert payload["videos"] == [{"url": "https://public.example/ref.mp4"}]


@pytest.mark.asyncio
async def test_flash_rejects_reference_video(monkeypatch):
    api = AgnesVideoAPI(api_key="configured", model="agnes-video-2.5-flash")

    with pytest.raises(ValueError, match="does not support reference videos"):
        await api.submit_video(
            prompt="flash",
            generation_mode="reference",
            reference_video_path="https://public.example/ref.mp4",
            duration=5,
            width=1280,
            height=720,
        )


@pytest.mark.asyncio
async def test_v25_rejects_more_than_three_reference_audio(monkeypatch):
    api = AgnesVideoAPI(api_key="configured", model="agnes-video-2.5")

    with pytest.raises(ValueError, match="at most 3 audio"):
        await api.submit_video(
            prompt="audio refs",
            generation_mode="reference",
            reference_audio_paths=[f"https://public.example/a{i}.mp3" for i in range(4)],
            duration=5,
            width=1280,
            height=720,
        )


@pytest.mark.asyncio
async def test_v25_i2v_alias_maps_to_documented_reference(monkeypatch):
    api = AgnesVideoAPI(api_key="configured", model="agnes-video-2.5-flash")
    captured = {}

    async def fake_resolve(path):
        return path

    async def fake_submit(payload, mode, progress_callback=None):
        captured["payload"] = payload
        return "video_test"

    monkeypatch.setattr(api, "_resolve_image_ref", fake_resolve)
    monkeypatch.setattr(api, "_submit_with_retry", fake_submit)
    monkeypatch.setattr(module, "normalize_reference_path", lambda path, width, height: path)

    await api.submit_video(
        prompt="reference",
        generation_mode="i2v",
        reference_image_paths=["ref.png"],
        duration=5,
        width=720,
        height=1280,
        video_size="720P",
    )

    assert captured["payload"]["mode"] == "reference"
    assert captured["payload"]["images"] == ["ref.png"]
