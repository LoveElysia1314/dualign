"""
Dualign — 嵌入编码器与模型加载

所有嵌入编码通过 API 后端（Ollama / LM Studio / 自定义 OpenAI 兼容 API）完成。
此模块仅负责编码器类和模型加载，不处理缓存。

缓存统一由 EmbeddingCache (SQLite 行级) 管理，见 embedding_cache.py。

导出:
    OllamaEncoder, OpenAICompatibleEncoder,
    load_model_for_provider, _try_lazy_load_model
"""

from __future__ import annotations

import os
import logging
import threading
from typing import Optional

import numpy as np

from dualign.config import (
    INSTRUCTION_TEXT,
)

logger = logging.getLogger(__name__)


class EncodingCancelled(RuntimeError):
    """Raised when a caller cancels an embedding request."""


# ── Ollama 配置 ──
OLLAMA_BASE_URL = os.environ.get("DUALIGN_OLLAMA_URL", "http://localhost:11434")

# 全局编码锁：Ollama 的 tokenizer 子进程无法承受高并发请求
# （实测 4 并发时 400 持续超过重试窗口），串行化 /api/embed 请求根治。
_OLLAMA_ENCODE_LOCK = threading.Lock()


# ═══════════════════════════════════════════════════════════════
# OllamaEncoder — 无需 torch/sentence-transformers 的嵌入后端
# ═══════════════════════════════════════════════════════════════


class OllamaEncoder:
    """通过 Ollama HTTP API 获取嵌入向量。

    用法:
        model = OllamaEncoder("qwen3-embedding:0.6b")
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=64)

    也可直接调用: vecs = model(["hello", "world"])
    """

    def __init__(
        self,
        model_name: str,
        base_url: str = OLLAMA_BASE_URL,
        instruction: Optional[str] = None,
    ):
        self._model = model_name
        self._url = base_url.rstrip("/")
        self._dim = None
        self._session = None
        # None 哨兵：未传入时自动使用全局 INSTRUCTION_TEXT
        self._instruction = instruction if instruction is not None else INSTRUCTION_TEXT

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    _TOKENIZE_RETRY_DELAYS = (1.0, 2.0)

    def _post_embed(self, batch):
        """串行发送单个 /api/embed 请求。"""
        with _OLLAMA_ENCODE_LOCK:
            resp = self.session.post(
                f"{self._url}/api/embed",
                json={"model": self._model, "input": batch},
                timeout=120,
            )
            resp.raise_for_status()
        return resp

    def encode(
        self,
        sentences,
        normalize_embeddings=True,
        batch_size=128,
        stop_event=None,
        **kwargs,
    ):
        texts = [sentences] if isinstance(sentences, str) else list(sentences)
        if not texts:
            return np.zeros((0, self._dim or 768))

        # ── Instruction 前缀 ──
        instruction = kwargs.pop("instruction", self._instruction)
        if instruction:
            texts = [instruction + t for t in texts]

        if not self._url:
            raise RuntimeError(
                "❌ Ollama API 地址为空\n" "   请在设置面板中配置 Ollama 服务地址"
            )
        if not self._model:
            raise RuntimeError(
                "❌ Ollama 模型名为空\n" "   请在设置面板中选择或输入嵌入模型名"
            )

        # ── 档位化 batch 收缩：仅当估算整批字符超限时才收缩到固定档位 ──
        # 实测 qwen3-embedding:0.6b 单请求约 100K 字符(32K tokens)为上限；
        # 默认 batch_size=128；较大的输入仍会按字符预算预先收缩。
        # 仅当 行均长 x batch_size 估算超限时，收缩到 ≤ 上限的最大档位。
        _HARD_LIMIT_CHARS = 90000  # 略低于实测 100K，留安全余量
        _BATCH_LEVELS = (128, 64, 32, 16, 8, 4, 2, 1)  # 收缩档位表
        _bs = max(1, min(int(batch_size), _BATCH_LEVELS[0]))
        if _bs > 1:
            _window = texts[: min(_bs * 2, 64)]  # 抽样估算行均长（封顶 64 行）
            _avg = sum(len(x) for x in _window) / max(len(_window), 1)
            _est_total = _avg * _bs
            if _est_total > _HARD_LIMIT_CHARS:
                # 按档位收缩：选 ≤ min(batch_size, limit/avg) 的最大档位
                _fit = int(_HARD_LIMIT_CHARS / max(_avg, 1))
                _bs = max(
                    (lv for lv in _BATCH_LEVELS if lv <= min(batch_size, _fit)),
                    default=batch_size,
                )
                if _bs < batch_size:
                    logger.debug(
                        "batch 档位收缩: %d -> %d (行均长 %d, 估算总量 %d > 上限 %d)",
                        batch_size,
                        _bs,
                        int(_avg),
                        int(_est_total),
                        _HARD_LIMIT_CHARS,
                    )

        all_embs = []
        i = 0
        single_tokenize_retries = 0
        while i < len(texts):
            # ── 检查停止信号（GUI 窗口关闭时中断编码）──
            if stop_event is not None and stop_event.is_set():
                raise EncodingCancelled("嵌入编码已取消")
            batch = texts[i : i + _bs]
            import requests as _requests

            try:
                resp = self._post_embed(batch)
            except _requests.exceptions.SSLError:
                raise RuntimeError(
                    f"❌ Ollama SSL 连接错误 ({self._url})\n"
                    f"   请检查网络环境或禁用 SSL 验证"
                )
            except _requests.exceptions.ConnectTimeout:
                raise RuntimeError(
                    f"❌ Ollama 连接超时 ({self._url})\n"
                    f"   请检查网络或 Ollama 是否已启动"
                )
            except _requests.exceptions.ConnectionError:
                raise RuntimeError(
                    f"❌ 无法连接到 Ollama ({self._url})\n"
                    f"   请确保 Ollama 已启动: ollama serve"
                )
            except _requests.exceptions.Timeout:
                raise RuntimeError(
                    f"❌ Ollama 请求超时 ({self._url})\n" f"   请检查网络或模型是否过大"
                )
            except _requests.exceptions.HTTPError as e:
                resp_obj = getattr(e, "response", None)
                status = resp_obj.status_code if resp_obj is not None else 0
                body = (getattr(resp_obj, "text", "") or "").strip()
                if status == 400 and "tokenize" in body.lower():
                    if len(batch) > 1:
                        previous_size = len(batch)
                        _bs = max(1, previous_size // 2)
                        logger.warning(
                            "Ollama tokenizer 拒绝 %d 条批次，自动收缩为 %d 条",
                            previous_size,
                            _bs,
                        )
                        single_tokenize_retries = 0
                        continue
                    if single_tokenize_retries < len(self._TOKENIZE_RETRY_DELAYS):
                        import time as _time

                        delay = self._TOKENIZE_RETRY_DELAYS[single_tokenize_retries]
                        single_tokenize_retries += 1
                        logger.warning(
                            "Ollama tokenizer 暂不可用，%.1fs 后重试单条请求",
                            delay,
                        )
                        _time.sleep(delay)
                        continue
                if status == 404:
                    raise RuntimeError(
                        f"❌ Ollama 模型未找到: {self._model}\n"
                        f"   请运行: ollama pull {self._model}"
                    )
                detail = f"\n   Ollama 响应: {body[:500]}" if body else ""
                raise RuntimeError(f"❌ Ollama API 错误 ({status}): {e}{detail}")
            except (
                _requests.exceptions.MissingSchema,
                _requests.exceptions.InvalidSchema,
                _requests.exceptions.InvalidURL,
                _requests.exceptions.URLRequired,
            ):
                raise RuntimeError(
                    f"❌ Ollama API 地址格式错误: {self._url}\n"
                    f"   请检查是否包含 http:// 或 https:// 前缀"
                )
            except _requests.exceptions.RequestException as e:
                raise RuntimeError(f"❌ Ollama 网络请求失败: {e}")
            try:
                data = resp.json()
                embs = [np.array(e, dtype=np.float32) for e in data["embeddings"]]
            except (KeyError, IndexError, TypeError):
                raise RuntimeError(
                    "❌ Ollama 响应格式异常: 未找到嵌入向量\n"
                    f"   请检查模型 {self._model} 是否支持嵌入编码"
                )
            except Exception as e:
                raise RuntimeError(f"❌ Ollama 响应解析失败: {e}")
            all_embs.extend(embs)
            i += len(batch)
            single_tokenize_retries = 0

        if not all_embs:
            return np.zeros((0, self._dim or 768))
        result = np.stack(all_embs)
        if self._dim is None and len(result) > 0:
            self._dim = result.shape[1]
        if normalize_embeddings:
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            norms = np.where(norms < 1e-12, 1.0, norms)
            result = result / norms
        return result

    def __call__(self, texts):
        return self.encode(texts, normalize_embeddings=True)


# ═══════════════════════════════════════════════════════════════
# OpenAICompatibleEncoder — /v1/embeddings 兼容端点
# ═══════════════════════════════════════════════════════════════


class OpenAICompatibleEncoder:
    """OpenAI /v1/embeddings 兼容编码器。

    适用于 LM Studio、硅基流动、DeepSeek、OpenAI 等。
    用法:
        model = OpenAICompatibleEncoder("http://localhost:1234", "my-model", api_key="sk-xxx")
        vecs = model.encode(texts)
    """

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: str = "",
        instruction: Optional[str] = None,
    ):
        self._url = base_url.rstrip("/") if base_url else ""
        self._model = model_name
        self._key = api_key
        self._dim = None
        # None 哨兵：未传入时自动使用全局 INSTRUCTION_TEXT
        self._instruction = instruction if instruction is not None else INSTRUCTION_TEXT

    def encode(
        self,
        sentences,
        normalize_embeddings=True,
        batch_size=256,
        stop_event=None,
        **kwargs,
    ):
        import requests

        texts = [sentences] if isinstance(sentences, str) else list(sentences)
        if not texts:
            return np.zeros((0, self._dim or 768))

        # ── Instruction 前缀 ──
        instruction = kwargs.pop("instruction", self._instruction)
        if instruction:
            texts = [instruction + t for t in texts]

        if not self._url:
            raise RuntimeError("❌ API 地址为空\n" "   请在设置面板中配置 API 服务地址")
        if not self._model:
            raise RuntimeError(
                "❌ 嵌入模型名为空\n" "   请在设置面板中选择或输入模型名"
            )

        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        all_embs = []
        for i in range(0, len(texts), batch_size):
            # ── 检查停止信号（GUI 窗口关闭时中断编码）──
            if stop_event is not None and stop_event.is_set():
                raise EncodingCancelled("嵌入编码已取消")
            batch = texts[i : i + batch_size]
            try:
                resp = requests.post(
                    f"{self._url}/v1/embeddings",
                    headers=headers,
                    json={"model": self._model, "input": batch},
                    timeout=120,
                )
                resp.raise_for_status()
            except requests.exceptions.SSLError:
                raise RuntimeError(
                    f"❌ API SSL 连接错误 ({self._url})\n"
                    f"   请检查网络环境或证书配置"
                )
            except requests.exceptions.ConnectionError:
                raise RuntimeError(
                    f"❌ 无法连接到 API 端点 ({self._url})\n" f"   请确保服务已启动"
                )
            except requests.exceptions.Timeout:
                raise RuntimeError(
                    f"❌ API 请求超时 ({self._url})\n" f"   请检查网络或模型是否过大"
                )
            except requests.exceptions.HTTPError as e:
                status = resp.status_code
                if status == 401 or status == 403:
                    raise RuntimeError(
                        f"❌ API 认证失败 ({status})\n" f"   请检查 API Key 是否正确"
                    )
                raise RuntimeError(f"❌ API 错误 ({status}): {e}")
            except (
                requests.exceptions.MissingSchema,
                requests.exceptions.InvalidSchema,
                requests.exceptions.InvalidURL,
                requests.exceptions.URLRequired,
            ):
                raise RuntimeError(
                    f"❌ API 地址格式错误: {self._url}\n"
                    f"   请检查是否包含 http:// 或 https:// 前缀"
                )
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f"❌ API 网络请求失败: {e}")
            try:
                data = resp.json()
                items = sorted(data["data"], key=lambda x: x.get("index", 0))
                embs = [np.array(item["embedding"], dtype=np.float32) for item in items]
            except (KeyError, IndexError, TypeError):
                raise RuntimeError(
                    "❌ API 响应格式异常: 未找到嵌入向量\n"
                    f"   请检查模型 {self._model} 是否支持嵌入编码"
                )
            except Exception as e:
                raise RuntimeError(f"❌ API 响应解析失败: {e}")
            all_embs.extend(embs)

        if not all_embs:
            return np.zeros((0, self._dim or 768))
        result = np.stack(all_embs)
        if self._dim is None and len(result) > 0:
            self._dim = result.shape[1]
        if normalize_embeddings:
            norms = np.linalg.norm(result, axis=1, keepdims=True)
            norms = np.where(norms < 1e-12, 1.0, norms)
            result = result / norms
        return result

    def __call__(self, texts):
        return self.encode(texts, normalize_embeddings=True)


# ═══════════════════════════════════════════════════════════════
# 模型缓存与加载
# ═══════════════════════════════════════════════════════════════

_MODEL_CACHE: dict = {}


def _effective_instruction(config) -> str:
    raw = getattr(config, "instruction_text", None)
    if raw:
        return raw
    return INSTRUCTION_TEXT if config.provider_id == "ollama" else ""


def _provider_cache_key(config, instruction: str) -> tuple[str, str, str, str, str]:
    """Identify a configured encoder by every request-affecting setting."""

    return (
        str(config.provider_id),
        str(config.base_url).rstrip("/"),
        str(config.model_name),
        instruction,
        str(getattr(config, "api_key", "")),
    )


def load_model_for_provider(config=None):
    """根据 ProviderConfig 加载编码器，结果缓存在 _MODEL_CACHE 中。

    若 config 为 None，自动使用 ProviderManager.active。
    兼容旧环境变量 DUALIGN_MODEL 作为回退。
    """
    if config is None:
        try:
            from dualign.providers import ProviderManager

            ProviderManager.load()
            config = ProviderManager.active()
        except Exception:
            pass

    if config is None:
        model_name = os.environ.get("DUALIGN_MODEL", "")
        if model_name:
            if model_name.startswith("ollama:"):
                ollama_name = model_name.split(":", 1)[1]
                cache_key = f"ollama_legacy:{ollama_name}"
                if cache_key in _MODEL_CACHE:
                    return _MODEL_CACHE[cache_key]
                model = OllamaEncoder(ollama_name, instruction=INSTRUCTION_TEXT)
                _MODEL_CACHE[cache_key] = model
                return model
            else:
                cache_key = f"ollama_legacy:{model_name}"
                if cache_key in _MODEL_CACHE:
                    return _MODEL_CACHE[cache_key]
                model = OllamaEncoder(model_name, instruction=INSTRUCTION_TEXT)
                _MODEL_CACHE[cache_key] = model
                return model
        from dualign.providers import DEFAULT_PROVIDERS

        logger.info("未找到已配置的嵌入提供方，使用默认 Ollama")
        config = DEFAULT_PROVIDERS[0]

    instr = _effective_instruction(config)
    cache_key = _provider_cache_key(config, instr)
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    pid = config.provider_id

    if pid == "ollama":
        model = OllamaEncoder(
            config.model_name, base_url=config.base_url, instruction=instr
        )
    elif pid == "lmstudio":
        model = OpenAICompatibleEncoder(
            config.base_url, config.model_name, instruction=instr
        )
    elif pid.startswith("custom_"):
        model = OpenAICompatibleEncoder(
            config.base_url,
            config.model_name,
            api_key=config.key_plain,
            instruction=instr,
        )
    else:
        raise ValueError(f"Unknown provider: {pid}")

    _MODEL_CACHE[cache_key] = model
    return model


def _try_lazy_load_model():
    """返回已缓存的模型，若未加载则同步加载。

    对于 GUI: 后台线程填充缓存后此函数快速返回。
    对于 CLI: 首次调用时同步加载（会阻塞几秒）。
    """
    try:
        from dualign.providers import ProviderManager

        ProviderManager.load()
        config = ProviderManager.active()
        if config is not None:
            return load_model_for_provider(config)
    except Exception as e:
        logger.warning("读取配置失败: %s", e)

    logger.info("未找到已配置的嵌入提供方，回退到默认 Ollama")
    return load_model_for_provider()
