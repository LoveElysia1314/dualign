"""
Dualign — Embedding 编码器容错测试

测试 OllamaEncoder / OpenAICompatibleEncoder 在各类异常场景下
是否全部通过 RuntimeError 返回友善的中文错误消息。
"""

from __future__ import annotations

import json as _json

import numpy as np
import pytest
import requests.exceptions as _req_exc

from dualign.services.embedding import (
    OllamaEncoder,
    OpenAICompatibleEncoder,
    load_model_for_provider,
)
from dualign.providers import ProviderConfig


class MockResponse:
    """可配置的 mock HTTP 响应。"""

    def __init__(self, status_code=200, json_data=None, raise_on_parse=False):
        self.status_code = status_code
        self._json_data = json_data or {}
        self._raise_on_parse = raise_on_parse
        self.text = str(json_data or "")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _req_exc.HTTPError(f"{self.status_code} Error", response=self)

    def json(self):
        if self._raise_on_parse:
            raise _json.JSONDecodeError("mock error", "", 0)
        return self._json_data


def _make_mock_session(post_func):
    """创建一个具有自定义 post 方法的 mock session。"""
    from unittest.mock import MagicMock

    session = MagicMock()
    session.post = post_func
    return session


def _raise_on_post(exc_cls):
    """返回一个 post 函数，调用时抛出指定异常。"""

    def _mock(*a, **kw):
        raise exc_cls

    return _mock


# ═══════════════════════════════════════════════════════════════
# 前置校验 — 不走网络
# ═══════════════════════════════════════════════════════════════


class TestOllamaPreChecks:
    def test_empty_url_raises_runtime_error(self):
        enc = OllamaEncoder("test", base_url="")
        with pytest.raises(RuntimeError, match="地址为空"):
            enc.encode(["hello"])

    def test_empty_model_raises_runtime_error(self):
        enc = OllamaEncoder("", base_url="http://localhost:11434")
        with pytest.raises(RuntimeError, match="模型名为空"):
            enc.encode(["hello"])

    def test_empty_texts_returns_zero_vector(self):
        enc = OllamaEncoder("test", base_url="http://localhost:11434")
        result = enc.encode([])
        assert result.shape == (0, 768)

    def test_url_trailing_slash_stripped(self):
        enc = OllamaEncoder("test", base_url="http://localhost:11434/")
        assert enc._url == "http://localhost:11434"


class TestOpenAIPreChecks:
    def test_empty_url_raises_runtime_error(self):
        enc = OpenAICompatibleEncoder("", "test")
        with pytest.raises(RuntimeError, match="地址为空"):
            enc.encode(["hello"])

    def test_none_url_becomes_empty(self):
        enc = OpenAICompatibleEncoder(None, "test")
        assert enc._url == ""

    def test_empty_model_raises_runtime_error(self):
        enc = OpenAICompatibleEncoder("http://localhost:1234", "")
        with pytest.raises(RuntimeError, match="模型名为空"):
            enc.encode(["hello"])

    def test_empty_texts_returns_zero_vector(self):
        enc = OpenAICompatibleEncoder("http://localhost:1234", "test")
        result = enc.encode([])
        assert result.shape == (0, 768)

    def test_url_trailing_slash_stripped(self):
        enc = OpenAICompatibleEncoder("http://localhost:1234/", "test")
        assert enc._url == "http://localhost:1234"


# ═══════════════════════════════════════════════════════════════
# requests 异常 — OllamaEncoder
# ═══════════════════════════════════════════════════════════════


class TestOllamaRequestErrors:
    """用 mock session 测试各类异常的正确包装。"""

    def _make_enc(self, exc_cls):
        enc = OllamaEncoder("test-model", base_url="http://localhost:11434")
        enc._session = _make_mock_session(_raise_on_post(exc_cls))
        return enc

    @pytest.mark.parametrize(
        "exc_cls,keywords",
        [
            (_req_exc.ConnectionError, ["无法连接", "Ollama"]),
            (_req_exc.Timeout, ["超时"]),
            (_req_exc.ReadTimeout, ["超时"]),
            (_req_exc.SSLError, ["SSL"]),
            (_req_exc.ConnectTimeout, ["连接超时"]),
            (_req_exc.TooManyRedirects, ["失败"]),
            (_req_exc.MissingSchema, ["格式错误"]),
        ],
    )
    def test_requests_exceptions(self, exc_cls, keywords):
        enc = self._make_enc(exc_cls)
        with pytest.raises(RuntimeError) as exc:
            enc.encode(["hello"])
        msg = str(exc.value)
        for kw in keywords:
            assert kw in msg, f"'{kw}' 未在: {msg}"

    def test_http_404(self):
        enc = self._make_enc(None)  # reset
        enc._session.post = lambda *a, **kw: MockResponse(status_code=404)
        with pytest.raises(RuntimeError) as exc:
            enc.encode(["hello"])
        assert "pull" in str(exc.value) or "未找到" in str(exc.value)


class TestOpenAIRequestErrors:
    """用 mock requests.post 测试各类异常的正确包装。"""

    @pytest.mark.parametrize(
        "exc_cls,keywords",
        [
            (_req_exc.ConnectionError, ["无法连接"]),
            (_req_exc.Timeout, ["超时"]),
            (_req_exc.SSLError, ["SSL"]),
        ],
    )
    def test_requests_exceptions(self, exc_cls, keywords):
        import requests as _req

        orig = _req.post
        _req.post = _raise_on_post(exc_cls)
        try:
            enc = OpenAICompatibleEncoder("http://localhost:1", "test")
            with pytest.raises(RuntimeError) as exc:
                enc.encode(["hello"])
            for kw in keywords:
                assert kw in str(exc.value), f"'{kw}' 未在: {str(exc.value)}"
        finally:
            _req.post = orig

    def test_http_401(self):
        import requests as _req

        orig = _req.post
        _req.post = lambda *a, **kw: MockResponse(status_code=401)
        try:
            enc = OpenAICompatibleEncoder("http://localhost:1", "test")
            with pytest.raises(RuntimeError) as exc:
                enc.encode(["hello"])
            assert "认证" in str(exc.value) or "401" in str(exc.value)
        finally:
            _req.post = orig

    def test_missing_schema(self):
        enc = OpenAICompatibleEncoder("localhost:1234", "test")
        with pytest.raises(RuntimeError, match="格式错误"):
            enc.encode(["hello"])


# ═══════════════════════════════════════════════════════════════
# 响应解析异常
# ═══════════════════════════════════════════════════════════════


class TestOllamaResponseParsing:
    @pytest.fixture
    def enc(self):
        e = OllamaEncoder("test", base_url="http://localhost:11434")
        e._session = _make_mock_session(None)
        return e

    def test_missing_embeddings_key(self, enc):
        enc._session.post = lambda *a, **kw: MockResponse(json_data={"model": "x"})
        with pytest.raises(RuntimeError, match="响应格式|嵌入"):
            enc.encode(["hello"])

    def test_bad_json_response(self, enc):
        enc._session.post = lambda *a, **kw: MockResponse(raise_on_parse=True)
        with pytest.raises(RuntimeError, match="响应解析"):
            enc.encode(["hello"])


class TestOpenAIResponseParsing:
    def test_missing_embedding_field(self):
        import requests as _req

        orig = _req.post
        _req.post = lambda *a, **kw: MockResponse(json_data={"data": [{"index": 0}]})
        try:
            enc = OpenAICompatibleEncoder("http://localhost:1234", "test")
            with pytest.raises(RuntimeError, match="响应格式|嵌入"):
                enc.encode(["hello"])
        finally:
            _req.post = orig

    def test_missing_data_key(self):
        import requests as _req

        orig = _req.post
        _req.post = lambda *a, **kw: MockResponse(json_data={})
        try:
            enc = OpenAICompatibleEncoder("http://localhost:1234", "test")
            with pytest.raises(RuntimeError, match="响应格式"):
                enc.encode(["hello"])
        finally:
            _req.post = orig


# ═══════════════════════════════════════════════════════════════
# Provider 路由
# ═══════════════════════════════════════════════════════════════


class TestLoadModelForProvider:
    @pytest.mark.parametrize(
        "pid,url,mname,expected",
        [
            ("ollama", "http://localhost:11434", "t", OllamaEncoder),
            ("lmstudio", "http://localhost:1234", "t", OpenAICompatibleEncoder),
            ("custom_1", "http://localhost:8080", "t", OpenAICompatibleEncoder),
        ],
    )
    def test_routing(self, pid, url, mname, expected):
        cfg = ProviderConfig(provider_id=pid, base_url=url, model_name=mname)
        assert isinstance(load_model_for_provider(cfg), expected)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            load_model_for_provider(
                ProviderConfig(provider_id="bogus", base_url="", model_name="")
            )

    def test_cache_hit(self):
        cfg = ProviderConfig(
            provider_id="ollama", base_url="http://l:11434", model_name="t"
        )
        m1 = load_model_for_provider(cfg)
        m2 = load_model_for_provider(cfg)
        assert m1 is m2

    def test_fallback_to_default(self):
        """无配置时回退到默认提供方（不崩溃即可）。"""
        from dualign.services.embedding import _try_lazy_load_model as _fallback

        model = _fallback()
        assert model is not None


class TestHealthCheck:
    def test_unreachable_ollama(self):
        from dualign.providers import ProviderManager

        ok, detail, _ = ProviderManager.health_check(
            ProviderConfig(
                provider_id="ollama", base_url="http://localhost:1", model_name="t"
            )
        )
        assert not ok

    def test_empty_url_custom(self):
        from dualign.providers import ProviderManager

        ok, detail, _ = ProviderManager.health_check(
            ProviderConfig(provider_id="custom_1", base_url="", model_name="t")
        )
        assert not ok

    def test_unknown_provider(self):
        from dualign.providers import ProviderManager

        ok, detail, _ = ProviderManager.health_check(
            ProviderConfig(provider_id="bogus", base_url="", model_name="")
        )
        assert not ok


class TestAiRepairAgentErrors:
    def test_missing_api_key(self):
        """API Key 为空且环境变量也未设置时抛出 ValueError。"""
        import os as _os

        old_key = _os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            from dualign.services.ai_repair_agent import DeepSeekNativeBackend

            backend = DeepSeekNativeBackend(api_key="")
            with pytest.raises(ValueError, match="API Key"):
                backend.chat(messages=[{"role": "user", "content": "hi"}])
        finally:
            if old_key is not None:
                _os.environ["DEEPSEEK_API_KEY"] = old_key

    def test_missing_openai_library(self):
        import builtins

        orig = builtins.__import__

        def _mock(name, *a, **kw):
            if name == "openai":
                raise ImportError("No module named 'openai'")
            return orig(name, *a, **kw)

        builtins.__import__ = _mock
        try:
            from dualign.services.ai_repair_agent import DeepSeekNativeBackend

            backend = DeepSeekNativeBackend(api_key="sk-test")
            with pytest.raises(ImportError, match="(?i)openai"):
                backend.chat(messages=[{"role": "user", "content": "hi"}])
        finally:
            builtins.__import__ = orig
