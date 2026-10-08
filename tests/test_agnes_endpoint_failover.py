"""Regression tests for Agnes endpoint selection and 401 domain failover."""

import json

import pytest

import core.api.rate_limiter as rl
from core.api.key_manager import KeyRing
from core.config import get_agnes_base_urls_for_key


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def test_agnes_base_urls_put_preferred_domain_first(monkeypatch, tmp_path):
    """A configured global domain stays first, but all Agnes-compatible endpoints remain available."""
    import core.config as config

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"agnes_domain": "cn"}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_FILE", str(config_path))

    urls = get_agnes_base_urls_for_key("k1")
    assert urls[0] == "https://api.agnes-ai.cn/v1"
    assert set(urls) == {
        "https://api.agnes-ai.cn/v1",
        "https://apihub.agnes-ai.cn/v1",
        "https://apihub.agnes-ai.com/v1",
    }


def test_request_with_key_rotation_fails_over_on_401(monkeypatch):
    """A key/domain mismatch must try another Agnes endpoint before failing."""
    ring = KeyRing(["k1"])
    monkeypatch.setattr(rl, "get_agnes_base_urls_for_key", lambda key: [
        "https://api.agnes-ai.cn/v1",
        "https://apihub.agnes-ai.com/v1",
    ])
    calls = []

    def requester(url, headers=None, **kwargs):
        calls.append((url, headers["Authorization"]))
        if len(calls) == 1:
            return FakeResponse(401, {"error": "unauthorized"})
        return FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})

    response = rl.request_with_key_rotation(
        requester,
        "/chat/completions",
        key_ring=ring,
        max_retries=0,
    )

    assert response.status_code == 200
    assert calls == [
        ("https://api.agnes-ai.cn/v1/chat/completions", "Bearer k1"),
        ("https://apihub.agnes-ai.com/v1/chat/completions", "Bearer k1"),
    ]


def test_request_with_key_rotation_preserves_401_if_all_endpoints_reject(monkeypatch):
    """If every compatible endpoint rejects the key, return the configured endpoint's 401."""
    ring = KeyRing(["k1"])
    monkeypatch.setattr(rl, "get_agnes_base_urls_for_key", lambda key: [
        "https://api.agnes-ai.cn/v1",
        "https://apihub.agnes-ai.com/v1",
    ])

    def requester(url, headers=None, **kwargs):
        return FakeResponse(401, {"error": "unauthorized"})

    response = rl.request_with_key_rotation(
        requester,
        "/chat/completions",
        key_ring=ring,
        max_retries=0,
    )

    assert response.status_code == 401
