# 文档审查报告：多提供方适配与整体质量

> 审查日期：2026-06-21 | 审查范围：全部 `.md` 文档（10 篇）
> 核心问题：项目代码已实现通用 ProviderManager（Ollama/LM Studio/自定义 API），
> 但文档大量表述仍隐含"Ollama 唯一"的过时假设。

---

## 一、代码中的真实设计

### 1.1 提供方系统架构

```
ProviderManager (providers.py)
  ├── 5 个插槽: ollama / lmstudio / custom_1 / custom_2 / custom_3
  ├── 持久化: providers.json (Fernet 加密 API Key)
  └── 健康检测: 连接 + 模型可用性

load_model_for_provider() (embedding.py)
  ├── ProviderManager.active() → 读配置
  ├── ollama  → OllamaEncoder    (HTTP /api/embed)
  ├── lmstudio → OpenAICompatibleEncoder (HTTP /v1/embeddings)
  ├── custom_* → OpenAICompatibleEncoder (HTTP /v1/embeddings + API Key)
  └── fallback: DUALIGN_MODEL env var → DEFAULT_PROVIDERS[0] (Ollama)
```

### 1.2 隐含业务规则

| 规则                                                     | 来源                               |
| -------------------------------------------------------- | ---------------------------------- |
| 嵌入提供方**可随时切换**（GUI 设置 → 下拉选择）          | `providers.py` ProviderManager     |
| API Key 本地加密存储，GUI 中可配置                       | `providers.py` Fernet encrypt      |
| LM Studio 与自定义 API 共用 `OpenAICompatibleEncoder`    | `embedding.py:368-372`             |
| 旧 `DUALIGN_MODEL` 环境变量仅作为**无 GUI 配置时的回退** | `embedding.py:330-345`             |
| AI 审校 Agent 与嵌入提供方**独立配置**（可不同后端）     | `providers.py` AiRepairAgentConfig |

---

## 二、逐文档审查

### 2.1 `README.md`（根）— 🔴严重问题，建议重写

| 位置     | 原文                                          | 问题                             | 建议修改                                                                                 |
| -------- | --------------------------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------- |
| L32      | "通过 **Ollama** 将句子转为语义向量"          | 隐含唯一性                       | "通过嵌入模型（Ollama / LM Studio / 兼容 API）将句子转为语义向量"                        |
| L47      | "支持 DeepSeek API 和 Ollama 本地模型双后端"  | AI审校后端混入嵌入描述           | "嵌入编码支持 Ollama / LM Studio / 自定义 API 多种后端"                                  |
| L101     | 架构图中 `Ollama 嵌入编码`                    | 抽象图不应有品牌名               | `嵌入编码 (Ollama / LM Studio / API)`                                                    |
| L141     | `\| **Ollama** \| 句子嵌入编码 \| ✅ 必须 \|` | **最严重问题**——品牌名充当依赖项 | `\| **嵌入后端服务** \|`，下含三个子选项 + `Ollama（默认·免费）🟢推荐`                   |
| L167     | `### 配置 Ollama`                             | 标题暗示唯一性                   | `### 配置嵌入后端`，三选项展开，Ollama 仍为默认·推荐                                     |
| L175     | 注脚只提 "任何 Ollama 嵌入模型"               | 忽略其他提供方                   | 展开为三选项 + 模型推荐（harrier 0.6b / qwen3-embedding:4b）                             |
| L213-215 | Python API 示例直接 `OllamaEncoder(...)`      | 跳过提供方管理层                 | 改为展示 `load_model_for_provider()` 或 `OllamaEncoder` + `OpenAICompatibleEncoder` 并提 |

**结构问题**：当前"快速开始"的层级为 `环境要求 → 安装 → 配置 Ollama → 启动 GUI → CLI → 批处理 → Python 库`，其中 **"配置 Ollama"作为一个顶级步骤**，给用户的暗示是"使用 Dualign 必须先搞 Ollama"。正确结构应为提供方配置作为一个可选展开，或者以"配置嵌入后端（选其一）"形式。

**重写建议**：将 §快速开始 的层级调整为：

```
环境要求（嵌入后端服务 / Python / API Key 三项并列）
安装
配置嵌入后端（折叠/选项形式，Ollama / LM Studio / 自定义 API）
启动 GUI
命令行对齐
批处理工作流
Python 库
```

### 2.2 `docs/quickstart.md` — 🟡中等，需重构

| 位置          | 问题                                                                    | 严重度                                           |
| ------------- | ----------------------------------------------------------------------- | ------------------------------------------------ |
| §嵌入后端配置 | 结构好（A/B/C 三项），但 **选项 A 标注"默认，推荐"** 暗示用户不应选 B/C | 🟡 移除"推荐"二字，改为"默认"                    |
| L97-99        | Python API 示例只展示 `OllamaEncoder`                                   | 🟡 增加 `load_model_for_provider()` 作为通用方式 |

**整体评价**：这篇是当前文档中平衡最好的，"嵌入后端配置"用 A/B/C 选项的形式本身就是正确的做法。小修即可。

### 2.3 `docs/README.md` — 🟢轻微，局部修补

仅 L47/L52 两处涉及，已用 "Ollama / LM Studio / 兼容 API" 的并列写法，基本正确。只需确保：

- L47 的依赖表已将 Ollama 从品牌名改为类别描述
- 批处理功能描述中的"原子 API"部分不提及具体提供方

**修补量**：~5 行改动。

### 2.4 `docs/user-guide.md` — 🟢轻微

受影响最小——GUI 操作指南以设置面板截图为依据，截图中 Ollama 条目高亮是事实描述，不应改。唯一需注意：

- L31 "嵌入后端（默认 Ollama）" → 精确但稍显侧重，可加脚注"可在设置面板切换"

**修补量**：~2 行。

### 2.5 `docs/reference.md` — 🟡中等

- L126-128：`OllamaEncoder` 直接出现在 §2.1 快速开始示例中
- L140：前置条件只提 Ollama

**修补量**：在 §2.1 增加 `load_model_for_provider()` 的使用示例，补充 LM Studio 编码器说明。

### 2.6 `docs/development.md` — 🟢轻微

L33-34 "启动嵌入后端：`ollama serve`" 是开发环境默认做法，作为单一示例可接受。L129-132 已同时展示 `OllamaEncoder` 和 `OpenAICompatibleEncoder`，做法正确。

### 2.7 `docs/faq.md` — 🟢轻微

L29 "端口冲突"只提 `DUALIGN_OLLAMA_URL`，但实际提供方地址已在 GUI 设置中可配。可补充一句"或在设置面板中修改 API 地址"。

### 2.8 `docs/algorithm.md` — ✅不变

纯算法讨论，不涉及部署/提供方。完全正确。

### 2.9 `demo/batch/README.md` — 🟢轻微

教学文档选择一种具体提供方（Ollama）作为示例是合理的，三份 `.md.py` 同理。

### 2.10 `docs/assets/images/README.md` — ✅不变

纯截图规格文档，截图内容就是 Ollama 条目，事实性描述不应更改。

---

## 三、整体质量评分

| 文档                   | 代码契合度 | 结构合理性 | 信息完整性 | 推荐行动                  |
| ---------------------- | ---------- | ---------- | ---------- | ------------------------- |
| `README.md`（根）      | ★★☆        | ★★☆        | ★★★☆       | 🔴 **重写"快速开始"章节** |
| `docs/quickstart.md`   | ★★★        | ★★★☆       | ★★★★       | 🟡 重构"嵌入后端配置"结构 |
| `docs/README.md`       | ★★★★       | ★★★★       | ★★★★       | 🟢 局部修补 5 行          |
| `docs/user-guide.md`   | ★★★★☆      | ★★★★★      | ★★★★★      | 🟢 局部修补 2 行          |
| `docs/reference.md`    | ★★★★       | ★★★★       | ★★★★☆      | 🟡 补充多提供方示例       |
| `docs/development.md`  | ★★★★       | ★★★★       | ★★★★       | 🟢 局部修补 3 行          |
| `docs/faq.md`          | ★★★        | ★★★★       | ★★★        | 🟢 局部修补 1 行          |
| `docs/algorithm.md`    | ★★★★★      | ★★★★★      | ★★★★★      | ✅ 不变                   |
| `demo/batch/README.md` | ★★★★       | ★★★★       | ★★★★       | 🟢 不变                   |

---

## 四、推荐优化方案

### 方案 A：最小干预（~2 小时，推荐）

只解决"Ollama 中心化"问题，不改文档结构：

| 文档                 | 改动量 | 改动内容                                                                                                                                  |
| -------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `README.md`          | ~20 行 | ① 环境要求表 `Ollama` → `嵌入后端服务` ② "配置 Ollama" → "配置嵌入后端"（三选项）③ 架构图中 `Ollama 嵌入编码` → `嵌入编码` ④ L32 文案修正 |
| `docs/quickstart.md` | ~10 行 | ① 移除"推荐"标签 ② 增加 `load_model_for_provider()` 示例                                                                                  |
| `docs/reference.md`  | ~10 行 | ① 增加 `OpenAICompatibleEncoder` 示例                                                                                                     |
| `docs/faq.md`        | ~2 行  | ① 端口兼容描述                                                                                                                            |
| 其他                 | ~3 行  | 零散修正                                                                                                                                  |

### 方案 B：中等整理（~4 小时，强烈推荐）

方案 A + 重构 `README.md` 的"快速开始"章节层级 + 重写 `docs/quickstart.md` 的"嵌入后端配置"段落：

1. **`README.md` 快速开始**：将"配置 Ollama"从独立步骤降级为"配置嵌入后端"下的一个折叠选项
2. **`docs/quickstart.md`**：改为"通用方式 → 按后端展开"的结构，先讲 `load_model_for_provider()` 再分后端举例

### 方案 C：全量重写（~8 小时，不推荐）

三篇文档全部重写，包括结构。不推荐因为大部分文档（user-guide, algorithm, reference）质量已达标。

**个人建议：方案 B** — 平衡性价比。核心是修好 `README.md` 的"快速开始"和 `quickstart.md`，让用户在 3 分钟内理解"Dualign 不绑定 Ollama"。

---

## 五、实际执行情况

> 方案 B 已落地，以下为 2026-06-21 真实修改记录。

### ✅ 已修改

| #   | 文件                     | 改动                                                               | 备注                                              |
| --- | ------------------------ | ------------------------------------------------------------------ | ------------------------------------------------- |
| 1   | `README.md`              | 环境要求表 `Ollama` → `嵌入后端服务`，下含三子选项                 | Ollama 标为"默认·免费 🟢推荐"                     |
| 2   | `README.md`              | `### 配置 Ollama` → `### 配置嵌入后端`                             | 三选项展开，Ollama 仍为默认推荐                   |
| 3   | `README.md`              | 配置内容改为 A/B/C 三选项                                          | 含模型推荐（harrier 0.6b / qwen3-embedding:4b）   |
| 4   | `README.md` L32          | "通过 Ollama" → "通过嵌入模型（Ollama / LM Studio / 兼容 API）"    |                                                   |
| 5   | `README.md`              | 架构图 `Ollama 嵌入编码` → `嵌入编码 (...)`                        | 删品牌，留后端类别                                |
| 6   | `README.md`              | Python 示例改用 `load_model_for_provider()`                        | 另附 OllamaEncoder / OpenAICompatibleEncoder 注释 |
| 7   | `docs/quickstart.md`     | "嵌入后端配置"保留选项 A 的"默认，推荐"                            | 用户要求保留推荐定位                              |
| 8   | `docs/quickstart.md`     | Python API 示例改用 `load_model_for_provider()`                    |                                                   |
| 9   | `docs/quickstart.md`     | 选项 A 下增加模型推荐（同 README）                                 | harrier 0.6b / qwen3-embedding:4b                 |
| 10  | `docs/reference.md` §2.1 | 增加 `load_model_for_provider()` 和 `OpenAICompatibleEncoder` 示例 |                                                   |
| 11  | `docs/faq.md`            | "端口冲突"补充"或在设置面板中修改"                                 |                                                   |

### 🔲 未修改（原因）

| 文件                           | 原因                                                    |
| ------------------------------ | ------------------------------------------------------- |
| `docs/README.md`               | 已使用"Ollama / LM Studio / 兼容 API"并列写法，符合要求 |
| `docs/user-guide.md`           | GUI 指南以截图事实为准，Ollama 截图是客观描述           |
| `docs/algorithm.md`            | 纯算法文档，不涉及部署/提供方                           |
| `docs/development.md`          | L129-132 已同时展示两种编码器，做法正确                 |
| `demo/batch/README.md`         | 教学文档取单一提供方示例是合理选择                      |
| `docs/assets/images/README.md` | 截图规格是事实性描述，不应更改                          |

### 📝 要点记存

- **Ollama 定位**：保持为默认·推荐（免费、零配置），但不是唯一选项
- **模型推荐**：
    - `leoipulsar/harrier-0.6b`（默认）— 基于微软 Qwen3-embedding 系列，0.6B 参数，大多数场景够用
    - `qwen3-embedding:4b-q4_K_M`（12GB+ 显存可选）— Ollama 官方有，质量更高但耗时约 3 倍
