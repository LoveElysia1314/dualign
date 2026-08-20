# 开发者指南

> 贡献者、自定义集成、打包发布相关文档。

---

## 目录

1. [环境搭建](#1-环境搭建)
2. [项目结构](#2-项目结构)
3. [代码质量与测试](#3-代码质量与测试)
4. [自定义嵌入模型](#4-自定义嵌入模型)
5. [自定义 AI 审校后端](#5-自定义-ai-审校后端)
6. [构建与打包](#6-构建与打包)

---

## 1. 环境搭建

```bash
git clone <repo-url>
cd dualign

# 同步开发环境（运行时依赖 + Black + pytest）
uv sync --extra dev

# 启动嵌入后端
ollama serve
ollama pull leoipulsar/harrier-0.6b
```

如果升级前已生成 `emb/{entry_id}/vecs.db`，执行：

```bash
uv run python scripts/migrate_embedding_cache.py --remove-legacy
```

脚本会先合并并逐库校验所有哈希，只在校验通过后删除旧缓存。

### 依赖分组

| 分组 | 命令                      | 包含                                  |
| ---- | ------------------------- | ------------------------------------- |
| 完整 | `pip install -e .`        | 对齐引擎 + CLI + AI 审校 + GUI 工作台 |
| 开发 | `uv sync --extra dev`       | 完整安装 + Black + pytest             |

---

## 2. 项目结构

```
dualign/
├── pyproject.toml
├── src/dualign/                 # 核心源码
│   ├── __init__.py              # 公共 API
│   ├── version.py               # 从包元数据读取版本
│   ├── __main__.py              # CLI 入口 (gui/align/check/models)
│   ├── common.py                # 工具函数 (hash/I/O/晋升)
│   ├── config.py                # 配置常量 + 缓存路径
│   ├── providers.py             # ProviderManager (Ollama/LM Studio/自定义)
│   │
│   ├── core/                    # 对齐引擎（纯函数）
│   │   ├── aligner.py           # Phase 1→5 DP 对齐
│   │   ├── punctuation.py       # 标点分割 + 语言检测
│   │   └── file_pair_matcher.py # 文件对发现
│   │
│   ├── models/                  # 数据模型
│   │   ├── state.py             # AlignmentSnapshot, ChapterState, etc.
│   │   ├── action.py            # RepairAction, AiProposal, AiProposalStore
│   │   ├── marker.py            # 操作标记编解码
│   │   └── snap_state.py        # SnapState 三层模型 + 审批四态
│   │
│   ├── services/                # 业务逻辑
│   │   ├── repair.py            # RepairState, replay(), auto_repair
│   │   ├── embedding.py         # OllamaEncoder / OpenAICompatibleEncoder
│   │   ├── embedding_cache.py   # SQLite 嵌入缓存
│   │   ├── cached_encoder.py    # 缓存代理
│   │   ├── similarity.py        # SimilarityScorer 评分器
│   │   ├── ai_repair_agent.py   # AiRepairAgent (tool-calling)
│   │   ├── quality_gate.py      # G1/G2/G3 质量门控
│   │   ├── cli_pipeline.py      # CLI 对齐流水线
│   │   ├── score_manager.py     # 异步评分管理器
│   │   └── prompts/             # Agent 提示词 + tools.json
│   │
│   └── gui/                     # PySide6 GUI
│       ├── window.py            # DualignWindow (主窗口)
│       ├── window_table.py      # 表格渲染
│       ├── window_actions.py    # 操作分发
│       ├── base_table.py        # 表格基础组件
│       ├── review.py            # ReviewController + AgentRunThread
│       ├── filter.py            # 双轴筛选
│       ├── dialogs.py           # 编辑/设置对话框
│       ├── panels.py            # DockPanelHelper
│       ├── settings.py          # DualignConfig
│       ├── workspace.py         # 工作区面板
│       ├── welcome.py           # 欢迎页
│       ├── status_bar.py        # 状态栏
│       ├── theme.py             # 主题系统
│       ├── focus.py             # FocusManager
│       ├── preview_table.py     # AI 建议预览
│       ├── workers.py           # 后台工作线程
│       └── text_hover.py        # 悬浮窗
│
├── tests/                       # 单元测试
├── demo/                        # 演示文件
└── docs/                        # 文档
```

---

## 3. 代码质量与测试

提交和发布前必须先用 Black 格式化仓库中的全部 Python 代码，再执行格式检查与测试：

```bash
# 格式化全部 Python 代码
uv run --extra dev black .

# 验证没有遗漏格式化
uv run --extra dev black --check .

# 全部测试
uv run --extra dev pytest tests/ -v

# 指定模块
uv run --extra dev pytest tests/test_align_core.py -v
uv run --extra dev pytest tests/test_repair_state.py -v

# 覆盖率
uv run --extra dev pytest tests/ --cov=src/dualign --cov-report=term-missing
```

---

## 4. 自定义嵌入模型

```python
from dualign.services.embedding import OllamaEncoder, OpenAICompatibleEncoder

# Ollama
model = OllamaEncoder("your-model-name")

# OpenAI 兼容 API（LM Studio / 自定义）
model = OpenAICompatibleEncoder(
    model_name="your-model",
    base_url="http://localhost:1234/v1",
    api_key="not-needed",
)
```

任何实现 `encode(texts, normalize_embeddings=True) → np.ndarray` 接口的对象均可作为模型传入对齐引擎。

环境变量快速切换：

```bash
set DUALIGN_MODEL=ollama:your-custom-model
```

切换提供方后缓存自动失效（缓存键含模型名 + instruction 哈希）。

---

## 5. 自定义 AI 审校后端

`AiRepairAgent` 支持通过 `LLMBackend` 抽象类接入自定义后端：

```python
from dualign.services.ai_repair_agent import AiRepairAgent, LLMBackend

class MyBackend(LLMBackend):
    def chat(self, messages, thinking=False, tools=None):
        # 实现 LLM 调用接口
        return LLMResponse(...)

agent = AiRepairAgent(backend=MyBackend())
```

当前内置 `DeepSeekNativeBackend`，使用 Responses API，并兼容本地 Ollama 的 `/v1/responses` 端点。

---

## 6. 构建与打包

### 一键完整构建（推荐）

```bash
python scripts/build.py
# → dist/dualign/                  PyInstaller 单文件夹
# → Dualign_Setup_v{VERSION}.exe   Inno Setup 安装包
# → Dualign_Portable_v{VERSION}.zip 便携版 ZIP（解压即用）
# → Dualign_Setup_v{VERSION}.zip    安装包 ZIP（Releases 分发）
```

### PyInstaller

```bash
pip install pyinstaller
python scripts/build_exe.py
# → dist/dualign/dualign.exe
```

### Inno Setup 安装程序

```bash
# 需安装 Inno Setup 6
python scripts/build_exe.py --installer
# → Dualign_Setup_v0.8.0.exe
```

### PyPI 发布

```bash
pip install build twine
python -m build
twine upload dist/*
```
