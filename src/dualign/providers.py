"""
Dualign — 模型提供方管理

ProviderManager（单例）管理所有嵌入模型提供方，支持：
- Ollama / LM Studio / 自定义 API
- API Key Fernet 加密存储
- 健康检测（连接 + 模型可用性）
- 从 providers.json 加载/保存
"""

from __future__ import annotations

import json
import os
import base64
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from dualign.config import APP_DATA_DIR, INSTRUCTION_TEXT

# ═══════════════════════════════════════════════════════════════
# 路径常量
# ═══════════════════════════════════════════════════════════════
PROVIDERS_PATH = os.path.join(APP_DATA_DIR, "providers.json")
REPAIR_AGENTS_PATH = os.path.join(APP_DATA_DIR, "repair_agents.json")
KEYFILE_PATH = os.path.join(APP_DATA_DIR, ".keyfile")


# ═══════════════════════════════════════════════════════════════
# ProviderConfig
# ═══════════════════════════════════════════════════════════════


@dataclass
class ProviderConfig:
    """单个模型提供方配置。"""

    provider_id: str = ""  # "ollama"|"lmstudio"|"custom_1"|"custom_2"|"custom_3"
    label: str = ""  # 显示名
    base_url: str = ""  # API 地址
    api_key: str = ""  # 加密后的 base64（仅在内存中解密为明文）
    model_name: str = ""  # 模型名
    is_enabled: bool = True  # 下拉列表中是否显示
    is_active: bool = False  # 当前是否选中
    instruction_text: str = ""  # 编码时拼接在文本前的 Instruction 前缀（空=不启用）

    # ── forward-compat：保留 from_dict 时未知的键 ──
    # 用户手动编辑 providers.json 添加 GUI 不支持的字段时，
    # 这些字段在 GUI 保存周期中不会丢失。
    _extra: dict = field(default_factory=dict, repr=False)

    # ── 已知字段白名单（to_dict / from_dict 使用）──
    _KNOWN_KEYS = frozenset(
        {
            "provider_id",
            "label",
            "base_url",
            "api_key",
            "model_name",
            "is_enabled",
            "is_active",
            "instruction_text",
        }
    )

    @property
    def key_plain(self) -> str:
        """解密 API Key（仅内存中）。"""
        if not self.api_key:
            return ""
        return ProviderManager.decrypt_key(self.api_key)

    def set_key_plain(self, plaintext: str):
        """加密并存储 API Key。"""
        self.api_key = ProviderManager.encrypt_key(plaintext) if plaintext else ""

    def to_dict(self) -> dict:
        result = {
            "provider_id": self.provider_id,
            "label": self.label,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model_name": self.model_name,
            "is_enabled": self.is_enabled,
            "is_active": self.is_active,
            "instruction_text": self.instruction_text,
        }
        # ── 合并 _extra 中的未知字段（forward-compat）──
        if self._extra:
            result.update(self._extra)
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderConfig":
        known = {
            "provider_id": d.get("provider_id", ""),
            "label": d.get("label", ""),
            "base_url": d.get("base_url", ""),
            "api_key": d.get("api_key", ""),
            "model_name": d.get("model_name", ""),
            "is_enabled": d.get("is_enabled", True),
            "is_active": d.get("is_active", False),
            "instruction_text": d.get("instruction_text", ""),
        }
        extra = {k: v for k, v in d.items() if k not in cls._KNOWN_KEYS}
        return cls(**known, _extra=extra)


# ═══════════════════════════════════════════════════════════════
# AiRepairAgentConfig — AI 修复 Agent 配置（独立于嵌入模型）
# ═══════════════════════════════════════════════════════════════


@dataclass
class AiRepairAgentConfig:
    """AI 修复 Agent 配置。"""

    agent_id: str = "deepseek_v4"
    label: str = "DeepSeek V4 Flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model_name: str = "deepseek-v4-flash"
    temperature: float = 0.0
    max_tokens: int = 393216
    is_enabled: bool = True
    is_active: bool = True
    note: str = ""

    @property
    def key_plain(self) -> str:
        if not self.api_key:
            return os.environ.get("DEEPSEEK_API_KEY", "")
        return ProviderManager.decrypt_key(self.api_key)

    def set_key_plain(self, plaintext: str):
        self.api_key = ProviderManager.encrypt_key(plaintext) if plaintext else ""

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "label": self.label,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "is_enabled": self.is_enabled,
            "is_active": self.is_active,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AiRepairAgentConfig":
        return cls(
            agent_id=d.get("agent_id", "deepseek_v4"),
            label=d.get("label", "DeepSeek V4 Flash"),
            base_url=d.get("base_url", ""),
            api_key=d.get("api_key", ""),
            model_name=d.get("model_name", ""),
            temperature=float(d.get("temperature", 0.0)),
            max_tokens=int(d.get("max_tokens", 4096)),
            is_enabled=d.get("is_enabled", True),
            is_active=d.get("is_active", True),
            note=d.get("note", ""),
        )


# ═══════════════════════════════════════════════════════════════
# 默认配置
# ═══════════════════════════════════════════════════════════════

DEFAULT_PROVIDERS = [
    ProviderConfig(
        provider_id="ollama",
        label="Ollama",
        base_url="http://localhost:11434",
        model_name="leoipulsar/harrier-0.6b",
        is_enabled=True,
        is_active=True,
        instruction_text=INSTRUCTION_TEXT,  # Ollama + qwen3-embedding 系模型支持 Instruction
    ),
    ProviderConfig(
        provider_id="lmstudio",
        label="LM Studio",
        base_url="http://localhost:1234",
        model_name="",
        is_enabled=True,
        is_active=False,
        instruction_text="",  # 默认关闭，用户可自行填写
    ),
    ProviderConfig(
        provider_id="custom_1",
        label="自定义 API 1",
        base_url="",
        model_name="",
        is_enabled=False,
        is_active=False,
        instruction_text="",
    ),
    ProviderConfig(
        provider_id="custom_2",
        label="自定义 API 2",
        base_url="",
        model_name="",
        is_enabled=False,
        is_active=False,
        instruction_text="",
    ),
    ProviderConfig(
        provider_id="custom_3",
        label="自定义 API 3",
        base_url="",
        model_name="",
        is_enabled=False,
        is_active=False,
        instruction_text="",
    ),
]


# ═══════════════════════════════════════════════════════════════
# 默认 AI 修复 Agent 配置
# ═══════════════════════════════════════════════════════════════

DEFAULT_REPAIR_AGENTS = [
    AiRepairAgentConfig(
        agent_id="deepseek_v4",
        label="DeepSeek V4 Flash",
        base_url="https://api.deepseek.com",
        model_name="deepseek-v4-flash",
        temperature=0.0,
        max_tokens=393216,
        is_active=True,
        note="推荐：支持工具调用、缓存命中，$0.14/1M tokens 输入。",
    ),
    AiRepairAgentConfig(
        agent_id="ollama_local",
        label="Ollama 本地 (qwen3.5:4b)",
        base_url="http://localhost:11434",
        model_name="qwen3.5:4b",
        temperature=0.0,
        max_tokens=393216,
        is_active=False,
        note="⚠ 不建议用于自动修复：工具调用能力不足，JSON 输出不稳定。仅适合对话模式。",
    ),
]

# ── 持久化 ──

_repair_agents: Optional[List[AiRepairAgentConfig]] = None


def load_repair_agents() -> List[AiRepairAgentConfig]:
    global _repair_agents
    if _repair_agents is not None:
        return _repair_agents
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    if os.path.isfile(REPAIR_AGENTS_PATH):
        try:
            with open(REPAIR_AGENTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            agents = [AiRepairAgentConfig.from_dict(d) for d in data]
            if agents:
                _repair_agents = agents
                return _repair_agents
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    import copy

    _repair_agents = copy.deepcopy(DEFAULT_REPAIR_AGENTS)
    save_repair_agents()
    return _repair_agents


def save_repair_agents():
    if _repair_agents is None:
        return
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    with open(REPAIR_AGENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(
            [a.to_dict() for a in _repair_agents], f, ensure_ascii=False, indent=2
        )


def active_repair_agent() -> Optional[AiRepairAgentConfig]:
    agents = load_repair_agents()
    for a in agents:
        if a.is_active:
            return a
    return None


def set_active_repair_agent(agent_id: str):
    agents = load_repair_agents()
    for a in agents:
        a.is_active = a.agent_id == agent_id
    save_repair_agents()


# ═══════════════════════════════════════════════════════════════
# 加密工具
# ═══════════════════════════════════════════════════════════════

_fernet = None


def _get_or_create_fernet():
    """懒加载 Fernet 实例（避免 import cryptography 时的开销）。"""
    global _fernet
    if _fernet is not None:
        return _fernet
    from cryptography.fernet import Fernet

    key = _get_or_create_key()
    _fernet = Fernet(base64.urlsafe_b64encode(key))
    return _fernet


def _get_or_create_key() -> bytes:
    """获取或生成 32 字节加密密钥。"""
    if os.path.exists(KEYFILE_PATH):
        try:
            with open(KEYFILE_PATH, "rb") as f:
                k = f.read()
                if len(k) == 32:
                    return k
        except (OSError, PermissionError):
            pass
    # 生成新密钥
    key = os.urandom(32)
    os.makedirs(os.path.dirname(KEYFILE_PATH), exist_ok=True)
    try:
        with open(KEYFILE_PATH, "wb") as f:
            os.chmod(KEYFILE_PATH, 0o600)
            f.write(key)
    except (OSError, PermissionError):
        # Windows: chmod 可能不支持，仍尝试写入
        pass
    return key


# ═══════════════════════════════════════════════════════════════
# ProviderManager（单例）
# ═══════════════════════════════════════════════════════════════


class ProviderManager:
    """全局提供方管理器（单例模式）。

    用法:
        ProviderManager.load()
        config = ProviderManager.active()
        ProviderManager.set_active("lmstudio")
        ProviderManager.save()
    """

    _providers: List[ProviderConfig] = []
    _loaded: bool = False

    @classmethod
    def load(cls) -> List[ProviderConfig]:
        """从 providers.json 加载配置。首次运行生成默认配置。"""
        if cls._loaded and cls._providers:
            return cls._providers
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        if os.path.isfile(PROVIDERS_PATH):
            try:
                with open(PROVIDERS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                providers = [ProviderConfig.from_dict(d) for d in data]
                if providers:  # 非空才使用
                    cls._providers = providers
                    # 确保至少有一个活跃提供方
                    if not any(p.is_active for p in cls._providers):
                        for p in cls._providers:
                            if p.is_enabled:
                                p.is_active = True
                                break
                    cls._loaded = True
                    return cls._providers
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        # 首次运行或配置为空：写入默认配置
        import copy

        cls._providers = copy.deepcopy(DEFAULT_PROVIDERS)
        cls.save()
        cls._loaded = True
        return cls._providers

    @classmethod
    def save(cls):
        """加密 Key 后写入 providers.json。"""
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        with open(PROVIDERS_PATH, "w", encoding="utf-8") as f:
            json.dump(
                [p.to_dict() for p in cls._providers], f, ensure_ascii=False, indent=2
            )

    @classmethod
    def reset_to_defaults(cls):
        """重置所有提供方配置到默认值并保存。"""
        import copy

        cls._providers = copy.deepcopy(DEFAULT_PROVIDERS)
        cls.save()

    @classmethod
    def active(cls) -> Optional[ProviderConfig]:
        """当前选中提供方。"""
        for p in cls._providers:
            if p.is_active:
                return p
        return None

    @classmethod
    def set_active(cls, provider_id: str):
        """切换活跃提供方。"""
        for p in cls._providers:
            p.is_active = p.provider_id == provider_id
        cls.save()

    @classmethod
    def get(cls, provider_id: str) -> Optional[ProviderConfig]:
        for p in cls._providers:
            if p.provider_id == provider_id:
                return p
        return None

    @classmethod
    def all_providers(cls) -> List[ProviderConfig]:
        return list(cls._providers)

    @classmethod
    def encrypt_key(cls, plaintext: str) -> str:
        """加密 API Key（AES-128-CBC + HMAC）。"""
        if not plaintext:
            return ""
        f = _get_or_create_fernet()
        return f.encrypt(plaintext.encode()).decode()

    @classmethod
    def decrypt_key(cls, ciphertext: str) -> str:
        """解密 API Key。"""
        if not ciphertext:
            return ""
        try:
            f = _get_or_create_fernet()
            return f.decrypt(ciphertext.encode()).decode()
        except Exception:
            # 密钥已变更或数据损坏
            return ""

    @classmethod
    def health_check(cls, config: ProviderConfig) -> Tuple[bool, str, List[str]]:
        """检测提供方是否可用。

        Returns:
            (ok, detail, models_available)
        """
        import requests as _requests

        pid = config.provider_id
        url = config.base_url.rstrip("/")

        try:
            if pid == "ollama":
                resp = _requests.get(f"{url}/api/tags", timeout=5)
                if resp.status_code != 200:
                    return False, f"Ollama 不可达 ({url})", []
                tags_data = resp.json()
                models = [m["name"] for m in tags_data.get("models", [])]
                model_ok = any(config.model_name in m for m in models)
                if model_ok:
                    return True, f"✓ Ollama 已连接，{config.model_name} 就绪", models
                return (
                    False,
                    f"⚠ Ollama 已连接但模型 {config.model_name} 未安装",
                    models,
                )

            elif pid == "lmstudio":
                resp = _requests.get(f"{url}/v1/models", timeout=5)
                if resp.status_code != 200:
                    return False, f"LM Studio 不可达 ({url})", []
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", [])]
                if models:
                    return True, f"✓ LM Studio 已连接，{len(models)} 个模型可用", models
                return False, "⚠ LM Studio 已连接但无可用模型", []

            elif pid.startswith("custom_"):
                if not url:
                    return False, "未配置 API 地址", []
                headers = (
                    {"Authorization": f"Bearer {config.key_plain}"}
                    if config.key_plain
                    else {}
                )
                resp = _requests.post(
                    f"{url}/v1/embeddings",
                    headers=headers,
                    json={"model": config.model_name, "input": ["test"]},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    dim = (
                        len(data.get("data", [{}])[0].get("embedding", []))
                        if data.get("data")
                        else 0
                    )
                    return True, f"✓ API 可用 (维度: {dim})", [config.model_name]
                return False, f"⚠ API 返回 {resp.status_code}: {resp.text[:200]}", []

            return False, f"未知提供方: {pid}", []

        except _requests.ConnectionError:
            return False, f"无法连接到 {url}", []
        except _requests.Timeout:
            return False, f"连接超时 ({url})", []
        except Exception as e:
            return False, f"检测失败: {e}", []

    @classmethod
    def model_name(cls) -> str:
        """便捷方法：当前活跃提供方的模型名。"""
        a = cls.active()
        return a.model_name if a else "unknown"

    @classmethod
    def provider_id(cls) -> str:
        """便捷方法：当前活跃提供方 ID。"""
        a = cls.active()
        return a.provider_id if a else "ollama"


# ═══════════════════════════════════════════════════════════════
# Ollama CLI 检测
# ═══════════════════════════════════════════════════════════════


def detect_ollama_cli() -> Tuple[bool, str]:
    """检测 Ollama 命令行工具是否已安装。

    Returns:
        (found, version_or_detail)
        found=True 时 version_or_detail 为版本号字符串；
        found=False 时 version_or_detail 为说明文字。
    """
    import subprocess as _sp

    try:
        r = _sp.run(
            ["ollama", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            ver = r.stdout.strip() or r.stderr.strip()
            return True, ver
        return False, "ollama 命令返回错误"
    except FileNotFoundError:
        return False, "未找到 ollama 命令（未安装或不在 PATH 中）"
    except PermissionError:
        return False, "ollama 可执行文件无执行权限"
    except OSError as e:
        return False, f"无法执行 ollama: {e}"
    except Exception as e:
        return False, f"检测失败: {e}"


# ═══════════════════════════════════════════════════════════════
# 纯函数: 解决方案指引（GUI 和 CLI 共用）
# ═══════════════════════════════════════════════════════════════


def build_solution_guidance(
    provider_id: str,
    detail: str,
    model_name: str = "",
    base_url: str = "",
) -> str:
    """根据健康检测结果生成人类可读的解决方案指引。

    纯函数，可被 GUI 对话框和 CLI 入口复用。

    Args:
        provider_id: "ollama" | "lmstudio" | "custom_*"
        detail:      health_check 返回的详情字符串
        model_name:  当前配置的模型名
        base_url:    当前配置的 API 地址

    Returns:
        多行指引文本。若无法识别问题则返回空字符串。
    """
    lines = []

    if "不可达" in detail or "无法连接" in detail or "拒绝连接" in detail:
        if provider_id == "ollama":
            lines = [
                "Ollama 服务未运行或未安装在当前机器。",
                "",
                "💡 解决方案：",
                "  1. 下载安装 Ollama：https://ollama.com",
                "  2. 启动服务：ollama serve",
                f"  3. 确认 {base_url or 'http://localhost:11434'} 可访问",
                f"  4. 拉取模型：ollama pull {model_name or 'qwen3-embedding:0.6b'}",
            ]
        elif provider_id == "lmstudio":
            lines = [
                "LM Studio 服务未运行。",
                "",
                "💡 解决方案：",
                "  1. 下载安装 LM Studio：https://lmstudio.ai",
                "  2. 加载一个嵌入模型（如 nomic-embed-text）",
                "  3. 启动 Local Server（默认端口 1234）",
                f"  4. 确认 {base_url or 'http://localhost:1234'} 可访问",
            ]
        elif provider_id.startswith("custom_"):
            lines = [
                f"自定义 API 端点不可达：{base_url}",
                "",
                "💡 解决方案：",
                "  1. 检查地址是否正确（需包含 http:// 或 https://）",
                "  2. 确认服务端已启动",
                "  3. 检查防火墙/代理设置",
                "  4. 如有 API Key，确认密钥有效",
            ]
        else:
            lines = [
                f"无法连接到 {base_url}",
                "",
                "💡 请检查服务是否已启动，地址是否正确。",
            ]

    elif "超时" in detail:
        lines = [
            f"连接超时：{base_url}",
            "",
            "💡 可能原因：",
            "  • 服务端响应过慢（首次加载模型可能需数十秒）",
            "  • 网络代理/防火墙拦截",
            "  • 地址拼写错误",
            "",
            "建议：检查服务端是否正在加载模型，稍后重试。",
        ]

    elif "模型" in detail and ("未安装" in detail or "未找到" in detail):
        if provider_id == "ollama":
            lines = [
                f"模型 {model_name} 未安装。",
                "",
                "💡 拉取模型：",
                f"  ollama pull {model_name or 'qwen3-embedding:0.6b'}",
            ]
        elif provider_id == "lmstudio":
            lines = [
                "LM Studio 中未找到可用嵌入模型。",
                "",
                "💡 搜索并下载嵌入模型，如：",
                "  • nomic-embed-text",
                "  • all-MiniLM-L6-v2",
                "",
                "然后在 Local Server 中加载该模型。",
            ]
        else:
            lines = [
                f"模型 {model_name} 不可用。",
                "💡 请确认模型名正确且已部署。",
            ]

    elif "返回" in detail and any(c in detail for c in ("4", "5")):
        lines = [
            detail,
            "",
            "💡 请检查：",
            "  • API 地址是否完整",
            "  • API Key 是否正确",
            "  • 模型名是否与服务端匹配",
        ]

    elif detail.startswith("✓"):
        lines = [detail, "", "✅ 一切正常，可以使用此提供方。"]

    return "\n".join(lines) if lines else ""
