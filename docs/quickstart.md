# Dualign 快速入门

> 5 分钟上手 — 从安装到完成第一次对齐

---

## 安装

```bash
git clone <repo-url>
cd dualign
pip install -e .
```

> 完整安装，包含对齐引擎、CLI、AI 审校和 GUI 工作台。

---

## 嵌入后端配置

Dualign 需要嵌入模型将句子转为语义向量。**首次使用请至少配置一个后端。**

### 选项 A：Ollama（默认，推荐）

```bash
# 安装：https://ollama.ai
ollama serve
ollama pull leoipulsar/harrier-0.6b
```

编码 500 行文本约需 3-5 秒（取决于硬件）。

> **模型推荐**：`leoipulsar/harrier-0.6b`（默认，0.6B 参数）编码够用，适合大多数场景。
> 12 GB+ 显存用户可选 `qwen3-embedding:4b-q4_K_M`，质量更高但耗时约 3 倍。
> 也支持任何 Ollama 嵌入模型：设置 `DUALIGN_MODEL=ollama:模型名` 即可切换。

### 选项 B：LM Studio

1. LM Studio 中加载嵌入模型
2. 启动本地推理服务器（默认 `http://localhost:1234`）
3. 在 Dualign 设置 → 模型配置中选择 "LM Studio"

### 选项 C：自定义 OpenAI 兼容 API

适用于硅基流动、DeepSeek Embedding 等。在设置中配置 `base_url`、`model_name` 和 API Key。

> **嵌入指令（Instruction）**：Dualign 支持在编码时自动在每行文本前拼接 Instruction 前缀，以提升 Qwen3-embedding 系列模型的语义区分度。
>
> - **GUI 设置**：模型配置 Tab → 勾选「启用 Instruction 前缀」并填入文本（支持按提供方独立配置）
> - **环境变量**：`DUALIGN_INSTRUCTION` 可设置自定义文本，置空禁用（适用于 CLI / 无 GUI 场景）
> - **默认文本**：`Instruct: Identify parallel sentences across languages\nQuery: `
> - **要求**：仅 Qwen3-embedding 系列或基于其微调的模型受益；其他模型可在 GUI 中关闭
> - 详见 [algorithm.md](algorithm.md#instruction-机制)。

---

## 最小工作流

### 环境检查

```bash
dualign check    # 环境健康检查
dualign models   # 列出可用模型
```

### CLI 一键对齐

```bash
dualign align -s 原文.md -t 译文.md -o ./output --strategy src
```

| 参数         | 说明                                                                  |
| ------------ | --------------------------------------------------------------------- |
| `-s`         | 原文文件路径                                                          |
| `-t`         | 译文文件路径                                                          |
| `-o`         | 输出目录（可选，默认当前目录）                                        |
| `--strategy` | 修复策略：`src`（原文优先）/ `tgt`（译文优先）/ `minimal`（最小变更） |

### GUI 交互式工作台

```bash
dualign gui
```

---

## 配置 AI 审校（可选）

```bash
# Windows PowerShell
$env:DEEPSEEK_API_KEY = "sk-your-key-here"

# Windows cmd
set DEEPSEEK_API_KEY=sk-your-key-here
```

> AI 审校目前仅支持 DeepSeek API。Ollama 仅作为嵌入编码后端。

---

## 作为 Python 库使用

```python
from dualign import RepairService
from dualign.services.embedding import load_model_for_provider

# 自动加载当前激活的提供方（默认 Ollama harrier-0.6b）
model = load_model_for_provider()

src_out, tgt_out, scores = RepairService.align_and_repair(
    ["第一章", "内容段落 A"],
    ["Chapter 1", "Content Para A"],
    model,
    strategy="minimal",
)
```

> 也可直接指定编码器：`OllamaEncoder` 或 `OpenAICompatibleEncoder`。
> 完整 API 参考见 [reference.md](reference.md#2-python-api-参考)。

---

## 下一步

| 文档                           | 适合谁     |
| ------------------------------ | ---------- |
| [user-guide.md](user-guide.md) | GUI 用户   |
| [reference.md](reference.md)   | 集成开发者 |
| [algorithm.md](algorithm.md)   | 算法贡献者 |
| [faq.md](faq.md)               | 遇到问题时 |
