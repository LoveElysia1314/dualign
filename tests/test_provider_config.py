"""
Dualign — 提供方配置管理测试

测试 ProviderConfig / AiRepairAgentConfig / ProviderManager /
AiRepairAgent 配置的序列化、加密、加载、默认值等。
"""

from __future__ import annotations

import copy
import os
import tempfile

import pytest

from dualign.providers import (
    ProviderConfig,
    AiRepairAgentConfig,
    ProviderManager,
    DEFAULT_PROVIDERS,
    DEFAULT_REPAIR_AGENTS,
    load_repair_agents,
    save_repair_agents,
    active_repair_agent,
    set_active_repair_agent,
    PROVIDERS_PATH,
    REPAIR_AGENTS_PATH,
)

# 备份和恢复 providers.json / repair_agents.json 的 fixture


@pytest.fixture(autouse=True)
def isolate_provider_files():
    """每个测试前备份配置文件，测试后恢复。"""
    bak_providers = None
    bak_agents = None
    if os.path.isfile(PROVIDERS_PATH):
        with open(PROVIDERS_PATH, "r", encoding="utf-8") as f:
            bak_providers = f.read()
    if os.path.isfile(REPAIR_AGENTS_PATH):
        with open(REPAIR_AGENTS_PATH, "r", encoding="utf-8") as f:
            bak_agents = f.read()

    # 清理内存状态
    ProviderManager._providers = []
    ProviderManager._loaded = False
    import dualign.providers as _mod

    _mod._repair_agents = None

    yield

    # 恢复文件
    if bak_providers is not None:
        with open(PROVIDERS_PATH, "w", encoding="utf-8") as f:
            f.write(bak_providers)
    elif os.path.isfile(PROVIDERS_PATH):
        os.remove(PROVIDERS_PATH)

    if bak_agents is not None:
        with open(REPAIR_AGENTS_PATH, "w", encoding="utf-8") as f:
            f.write(bak_agents)
    elif os.path.isfile(REPAIR_AGENTS_PATH):
        os.remove(REPAIR_AGENTS_PATH)

    # 清理内存
    ProviderManager._providers = []
    ProviderManager._loaded = False
    _mod._repair_agents = None


# ═══════════════════════════════════════════════════════════════
# ProviderConfig 单元测试 (不依赖文件)
# ═══════════════════════════════════════════════════════════════


class TestProviderConfig:
    def test_default_ollama_config(self):
        cfg = DEFAULT_PROVIDERS[0]
        assert cfg.provider_id == "ollama"
        assert cfg.base_url == "http://localhost:11434"
        assert cfg.model_name == "leoipulsar/harrier-0.6b"

    def test_default_lmstudio_config(self):
        assert DEFAULT_PROVIDERS[1].provider_id == "lmstudio"

    def test_default_custom_disabled(self):
        for i in range(2, 5):
            assert DEFAULT_PROVIDERS[i].is_enabled is False

    def test_to_dict_round_trip(self):
        cfg = ProviderConfig(
            provider_id="ollama",
            label="Test",
            base_url="http://localhost:11434",
            model_name="test-model",
            is_active=True,
        )
        restored = ProviderConfig.from_dict(cfg.to_dict())
        assert restored.provider_id == cfg.provider_id
        assert restored.model_name == cfg.model_name
        assert restored.is_active is True

    def test_encrypted_key_round_trip(self):
        cfg = ProviderConfig(provider_id="custom_1")
        plain = "sk-test-key-12345"
        cfg.set_key_plain(plain)
        assert cfg.api_key != plain  # 加密后不同
        assert cfg.key_plain == plain  # 解密后还原

    def test_empty_key_plain_returns_empty(self):
        assert ProviderConfig(provider_id="custom_1").key_plain == ""

    def test_from_dict_missing_fields_use_defaults(self):
        restored = ProviderConfig.from_dict({"provider_id": "ollama"})
        assert restored.base_url == ""
        assert restored.is_active is False


# ═══════════════════════════════════════════════════════════════
# AiRepairAgentConfig 单元测试
# ═══════════════════════════════════════════════════════════════


class TestAiRepairAgentConfig:
    def test_default_deepseek_config(self):
        cfg = DEFAULT_REPAIR_AGENTS[0]
        assert cfg.agent_id == "deepseek_v4"
        assert cfg.base_url == "https://api.deepseek.com"
        assert cfg.model_name == "deepseek-v4-flash"

    def test_to_dict_round_trip(self):
        cfg = AiRepairAgentConfig(
            agent_id="custom",
            label="Custom",
            base_url="https://api.example.com",
            model_name="gpt-4",
            temperature=0.5,
            is_active=False,
        )
        restored = AiRepairAgentConfig.from_dict(cfg.to_dict())
        assert restored.agent_id == cfg.agent_id
        assert restored.temperature == 0.5
        assert restored.is_active is False

    def test_from_dict_missing_fields(self):
        restored = AiRepairAgentConfig.from_dict({})
        assert restored.agent_id == "deepseek_v4"
        assert restored.is_active is True

    def test_key_plain_fallback_to_env(self):
        import os as _os

        old = _os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            cfg = AiRepairAgentConfig(api_key="")
            assert cfg.key_plain == ""  # 无 env 时为空
            _os.environ["DEEPSEEK_API_KEY"] = "env-key"
            assert cfg.key_plain == "env-key"
        finally:
            if old is not None:
                _os.environ["DEEPSEEK_API_KEY"] = old
            else:
                _os.environ.pop("DEEPSEEK_API_KEY", None)


# ═══════════════════════════════════════════════════════════════
# ProviderManager 集成测试
# ═══════════════════════════════════════════════════════════════


class TestProviderManager:
    def test_load_creates_defaults(self):
        providers = ProviderManager.load()
        assert len(providers) == 5
        # 首次加载时第一个 enabled 的应为 active
        assert any(p.is_active for p in providers)

    def test_active_returns_selected(self):
        ProviderManager.load()
        active = ProviderManager.active()
        assert active is not None
        assert active.is_active is True

    def test_set_active_changes_selection(self):
        ProviderManager.load()
        ProviderManager.set_active("lmstudio")
        active = ProviderManager.active()
        assert active.provider_id == "lmstudio"

    def test_get_returns_correct_provider(self):
        ProviderManager.load()
        assert ProviderManager.get("ollama").provider_id == "ollama"

    def test_get_nonexistent_returns_none(self):
        ProviderManager.load()
        assert ProviderManager.get("nonexistent") is None

    def test_model_name_convenience(self):
        ProviderManager.load()
        # 确保 active provider 有模型名
        active = ProviderManager.active()
        if not active or not active.model_name:
            # 切换到 ollama（它有默认模型名）
            ProviderManager.set_active("ollama")
            ProviderManager._loaded = True  # 复用已加载状态
        name = ProviderManager.model_name()
        assert len(name) > 0, f"模型名为空, active={ProviderManager.active()}"

    def test_provider_id_convenience(self):
        ProviderManager.load()
        pid = ProviderManager.provider_id()
        assert pid in ("ollama", "lmstudio", "custom_1", "custom_2", "custom_3")

    def test_encrypt_decrypt_key(self):
        plain = "sk-abcdef123456"
        encrypted = ProviderManager.encrypt_key(plain)
        assert encrypted != plain
        assert ProviderManager.decrypt_key(encrypted) == plain

    def test_decrypt_bad_cipher_returns_empty(self):
        assert ProviderManager.decrypt_key("bad-cipher") == ""

    def test_provider_manager_reuse(self):
        ProviderManager._providers = [DEFAULT_PROVIDERS[0]]
        ProviderManager._loaded = True
        providers = ProviderManager.load()
        assert len(providers) == 1  # 不是 5

    def test_provider_defaults_not_mutated(self):
        original_id = DEFAULT_PROVIDERS[0].provider_id
        p = copy.deepcopy(DEFAULT_PROVIDERS[0])
        p.provider_id = "modified"
        assert DEFAULT_PROVIDERS[0].provider_id == original_id


# ═══════════════════════════════════════════════════════════════
# Repair Agent 配置管理
# ═══════════════════════════════════════════════════════════════


class TestRepairAgentConfigManagement:
    def test_load_repair_agents_returns_defaults(self):
        agents = load_repair_agents()
        assert len(agents) >= 1
        assert agents[0].agent_id == "deepseek_v4"

    def test_active_repair_agent(self):
        """active_repair_agent 不崩溃即可。"""
        load_repair_agents()
        try:
            active_repair_agent()
        except Exception:
            pytest.fail("active_repair_agent() 抛出了异常")

    def test_set_active_repair_agent_does_not_crash(self):
        load_repair_agents()
        # 设置一个不存在的 id — 不崩溃即可
        set_active_repair_agent("nonexistent")
        # 再次加载应正常工作
        try:
            active_repair_agent()
        except Exception:
            pytest.fail("active_repair_agent() 抛出了异常")

    def test_save_and_reload_preserves_config(self):
        agents_before = load_repair_agents()
        original_count = len(agents_before)
        agents_before[0].temperature = 0.7
        save_repair_agents()
        import dualign.providers as _mod

        _mod._repair_agents = None  # 清除缓存
        agents_after = load_repair_agents()
        assert len(agents_after) == original_count
        # 恢复
        agents_after[0].temperature = 0.0
        save_repair_agents()
