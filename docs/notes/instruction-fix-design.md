# Instruction 机制审查报告

> 分析 Instruction 前缀在 Dualign 各编码器中的实现、副作用、缓存影响，以及改进方案。

---

## 一、现状：代码实况

### 1.1 数据流

```python
# config.py — 全局硬编码
_DEFAULT_INSTRUCTION = "Instruct: Identify parallel sentences across languages\nQuery: "
INSTRUCTION_TEXT = os.environ.get("DUALIGN_INSTRUCTION", _DEFAULT_INSTRUCTION)

# embedding.py — 两编码器均无条件使用
class OllamaEncoder:
    def __init__(self, model_name, base_url, instruction=None):
        self._instruction = instruction if instruction is not None else INSTRUCTION_TEXT

class OpenAICompatibleEncoder:
    def __init__(self, base_url, model_name, api_key="", instruction=None):
        self._instruction = instruction if instruction is not None else INSTRUCTION_TEXT

# 编码时：直接将 instruction 拼接到每行文本前
def encode(self, sentences, ...):
    instruction = kwargs.pop("instruction", self._instruction)
    if instruction:
        texts = [instruction + t for t in texts]  # ← 无条件拼接
```

### 1.2 Cache 键设计（正确的部分）

```python
# cached_encoder.py
self._instr_hash = _instruction_hash(INSTRUCTION_TEXT)  # 全局常量
self._key_prefix = f"{model_name}_{self._instr_hash}"    # 模型+指令联合键

hash = f"{content_hash(text)}_{self._key_prefix}"        # 行级缓存键
```

**优点**：Instruction 变化 → `_instr_hash` 变化 → 缓存键自动变化 → 缓存自然失效。不会出现"变了 instruction 还命中旧缓存"的问题。

### 1.3 现状关键特征

| 特征                     | 值                                                                       |
| ------------------------ | ------------------------------------------------------------------------ |
| Instruction 对谁生效     | **所有提供方** — Ollama / LM Studio / 自定义 API 均被拼接                |
| 是否有校验逻辑           | **无** — 不检查模型是否支持                                              |
| 是否会引发 API 错误      | **取决于后端** — 大多数 API 端不会拒绝"带前缀的文本"，但嵌入质量可能下降 |
| 是否可被用户关闭         | 仅通过环境变量 `DUALIGN_INSTRUCTION=""` 全局关闭                         |
| 是否有 GUI 控制          | **无** — 设置面板中没有此选项                                            |
| 是否可 per-provider 配置 | **否** — 全局单一值                                                      |

---

## 二、副作用分析

### 2.1 对 Ollama + qwen3-embedding 系模型（含默认 harrier）

**效果**：正面。这些模型在训练时使用了 Instruction 机制，前缀能提升嵌入的语义区分度（摸底结果 Δ ≥ 0.10）。

**风险**：无。

### 2.2 对 Ollama + 通用嵌入模型（如 nomic-embed-text, all-MiniLM-L6-v2 等非 qwen3 系）

**效果**：中性或负面。这些模型**未经过 Instruction 训练**，前缀只是作为文本的一部分被编码。可能轻微影响（引入无关 token），但通常不会显著劣化——因为嵌入模型对这种"多余前缀"有一定鲁棒性。

### 2.3 对 LM Studio / 自定义 API（OpenAI 兼容）

| 场景                                    | 效果                           |
| --------------------------------------- | ------------------------------ |
| LM Studio + qwen3-embedding 系          | ✅ 正面，同 Ollama             |
| LM Studio + 通用 BERT 系                | ➖ 中性，前缀被忽略            |
| **OpenAI text-embedding-3-small/large** | ⚠️ 未知——OpenAI 官方不推荐前缀 |
| 硅基流动 / DeepSeek Embedding           | ⚠️ 取决于具体模型文档          |

### 2.4 副作用总结

**不是报错问题**，是"可能做了无用功甚至拖累质量"的问题。API 端不会因为多了一段前缀而拒绝请求（除了极少数严格校验输入的端点），但嵌入质量可能未达最佳。

---

## 三、最干净的改动方案

### 3.1 思路

让 Instruction 成为提供方级别的可配置属性，而非全局常量。这样：

- qwen3-embedding 系模型：启用 Instruction（默认）
- 其他模型：可在 GUI 中关闭
- 缓存键：使用实际生效的 instruction 文本（而非全局常量）构建，自动区分

### 3.2 具体改动

#### step 1: `providers.py` — ProviderConfig 增加字段

```python
@dataclass
class ProviderConfig:
    provider_id: str = ""
    label: str = ""
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    is_enabled: bool = True
    is_active: bool = False
    # ★ 新增
    instruction_text: str = ""   # 空字符串 = 不传 instruction
    # ★ 或简单布尔值
    # instruction_enabled: bool = True
```

默认值策略：

- `ollama` 提供方：默认 `"Instruct: Identify parallel sentences across languages\nQuery: "`（保持当前行为）
- 其他提供方：默认 `""`（不启用，safe by default）

#### step 2: `embedding.py` — `load_model_for_provider()` 传入 per-provider instruction

```python
def load_model_for_provider(config=None):
    ...
    instr = config.instruction_text or None  # 空串 → None → 编码器不拼接

    if pid == "ollama":
        model = OllamaEncoder(config.model_name, base_url=config.base_url, instruction=instr)
    elif pid == "lmstudio":
        model = OpenAICompatibleEncoder(config.base_url, config.model_name, instruction=instr)
    elif pid.startswith("custom_"):
        model = OpenAICompatibleEncoder(config.base_url, config.model_name,
                                         api_key=config.key_plain, instruction=instr)
```

#### step 3: `cached_encoder.py` — 缓存键使用实际 instruction 文本

```python
# 当前（绑死全局常量）：
self._instr_hash = _instruction_hash(INSTRUCTION_TEXT)

# 改为（使用编码器实际使用的 instruction）：
actual_instruction = getattr(encoder, "_instruction", "") or ""
self._instr_hash = _instruction_hash(actual_instruction)
```

这样不同提供方使用不同 instruction 时缓存键自然隔离。

#### step 4: `gui/settings.py` — 设置面板增加 Instruction 配置

在嵌入模型配置的每个条目中增加一个可选的 Instruction 文本框，默认填充当前全局值。用户可清空来禁用，或填入自定义值。

**复杂度评估**：~50 行 Python（GUI 组件 + 序列化 + 加载逻辑）。

### 3.3 向后兼容

| 场景                                   | 行为变化                                                                                                                   |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| 已配置 Ollama（无显式 instruction）    | ✅ 不变 — 默认值保持当前文本                                                                                               |
| 已配置自定义 API（无显式 instruction） | ⚠️ 行为变化：Instruction 从"全局启用"变为"默认关闭"。这是期望的修正——自定义 API 用户本就不该默认使用 Ollama 的 instruction |
| 环境变量 `DUALIGN_INSTRUCTION`         | 保留作为全局回退。仅当提供方自身的 `instruction_text` 为空时才读取该环境变量                                               |

---

## 四、文档中的模型推荐补充

文档中建议在 Ollama 配置选项下补充：

> **嵌入模型选择建议**：`leoipulsar/harrier-0.6b`（默认，基于 Qwen3-embedding 微调）在轻小说场景下表现均衡。对于本地部署，推荐优先选用 **Qwen3-embedding** 系列或基于其微调的模型（如 harrier），它们原生支持 Instruction 前缀机制，可充分发挥 Dualign 的嵌入增强能力。其他通用嵌入模型（如 nomic-embed-text）也可使用，但 Instruction 前缀带来的增益有限——可在设置面板中按需关闭。

---

## 五、改动成本评估

| 文件                                       | 改动量                                     | 风险                             |
| ------------------------------------------ | ------------------------------------------ | -------------------------------- |
| `providers.py`                             | +3 行（字段 + 默认值）                     | 低 — 纯数据类扩展                |
| `embedding.py` `load_model_for_provider()` | +1 行（读取 config 的 instruction）        | 低                               |
| `cached_encoder.py`                        | +2 行（使用实际 instruction 而非全局常量） | 低 — 不影响无 instruction 的场景 |
| `gui/settings.py`                          | ~30 行（新增 Instruction 输入框）          | 中 — 需测试序列化/反序列化路径   |
| `config.py`                                | 不变                                       | —                                |

**总成本：约半天 ~ 一天。**

如果觉得这个改动在 v0.7.0 正式发布前优先级不高（当前行为虽然在非 qwen3 模型上做了无用功但不会导致错误），可以先：

1. **只改文档**：补充 Instruction 机制说明和模型推荐
2. **不改代码**：等下有 GUI 重构窗口期再处理
