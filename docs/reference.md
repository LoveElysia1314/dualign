# Dualign 0.7.0 参考手册

> 技术架构 · Python API · 数据格式规范 — 面向集成开发者

---

## 目录

1. [技术架构](#1-技术架构)
2. [Python API 参考](#2-python-api-参考)
3. [数据格式规范](#3-数据格式规范)

---

## 1. 技术架构

### 1.1 数据流全貌

```text
                         输入
  source.md (原文)          target.md (译文)
               │                    │
               ▼                    ▼
          句子嵌入编码 (Ollama / LM Studio / OpenAI 兼容 API)
               │                    │
               ▼                    ▼
                 对齐引擎 (v0.7.0)
         Phase 1→5 流水线: 递归锚点 → 赝锚点 →
         合并枚举 → 批量编码 → 单一 DP
               │
               ▼
        AlignmentSnapshot (不可变快照)
               │
               ▼
          修复层 (RepairService)
  自动修复 → AI 校订 → 交互式修复
               │
               ▼
           RepairState (不可变状态容器)
           snapshot + append-only repair_log
               │
               ▼
           ChapterState (重放结果)
           SnapGroup[] → AlignedRow[]
               │
               ▼
         make_table_view() → TableViewModel
               │
               ▼
           GUI _render_table() 渲染
```

### 1.2 四层架构

```text
L1 对齐引擎 (core/aligner.py)
  └→ Phase 1→5 流水线: 递归锚点 → 赝锚点 → 合并枚举 → 批量编码 → 单一 DP
L2 自动修复 (services/repair.py)
  └→ 规则流水线处理 N:1/1:M/1:0/0:1
L3 AI 审校 (services/ai_repair_agent.py)
  └→ DeepSeek API 多轮语义审校 (tool-calling)
L4 交互式 GUI (gui/window.py)
  └→ PySide6 工作台，人工终审
```

### 1.3 核心设计原则

**不可变状态**：`RepairState = AlignmentSnapshot + append-only RepairAction[] + 纯函数 replay()`。`undo()` 只需移除 `repair_log` 最后一条。

**外部索引永不变化**：`snap_i` 始终指向 `AlignmentSnapshot.original_ops[snap_i]`。

**双轴异常系统**：

- 轴 1 — 异常类型（客观属性）：`NON_1TO1` / `MIX` / `LOW_SCORE` / `FLAGGED`
- 轴 2 — 审批状态（用户行为）：`none` / `auto` / `agent` / `user`

**info-free vs info-full 操作**：

| 类别      | 操作                                 | 存储内容                           |
| --------- | ------------------------------------ | ---------------------------------- |
| info-free | merge, delete, placeholder, flag, ok | 仅 marker 标记                     |
| info-full | edit, split                          | 完整 new_src_lines / new_tgt_lines |

### 1.4 核心类

| 类                                          | 模块                          | 职责                            |
| ------------------------------------------- | ----------------------------- | ------------------------------- |
| `AlignmentSnapshot`                         | `models/state.py`             | 不可变对齐快照                  |
| `RepairAction`                              | `models/action.py`            | 8 种操作类型                    |
| `RepairState`                               | `services/repair.py`          | 状态容器：snapshot + repair_log |
| `ChapterState` / `SnapGroup` / `AlignedRow` | `models/state.py`             | 重放后表格数据                  |
| `SnapState`                                 | `models/snap_state.py`        | 三层模型 + 审批四态             |
| `AiRepairAgent`                             | `services/ai_repair_agent.py` | Tool-calling 代理               |
| `AiProposal` / `AiProposalStore`            | `models/action.py`            | AI 建议存储                     |

### 1.5 异常/审批系统

**异常类型（轴 1）**：

| 常量        | 含义          | 严重度 |
| ----------- | ------------- | ------ |
| `NON_1TO1`  | 非 1:1 文本对 | 错误   |
| `MIX`       | 语言杂糅风险  | 错误   |
| `LOW_SCORE` | 离群低分      | 需核实 |
| `FLAGGED`   | 手动标记异常  | 错误   |

**审批状态（轴 2）**：

| 常量    | 含义             |
| ------- | ---------------- |
| `none`  | 未处理           |
| `auto`  | 自动修复，待审批 |
| `agent` | AI 审校已处理    |
| `user`  | 用户已手动处理   |

审批为四态递进管线：`none → auto → agent → user`（flag 不推进管线）。

---

## 2. Python API 参考

### 2.1 快速开始

```python
from dualign import RepairService
from dualign.services.embedding import load_model_for_provider

# 自动加载当前配置的提供方（默认 Ollama harrier-0.6b）
model = load_model_for_provider()

src_out, tgt_out, scores = RepairService.align_and_repair(
    ["第一章", "内容段落 A", "内容段落 B"],
    ["Chapter 1", "Content Para A"],
    model,
    strategy="minimal",
)
```

也可直接指定编码器：

```python
from dualign.services.embedding import OllamaEncoder, OpenAICompatibleEncoder

# Ollama
model = OllamaEncoder("leoipulsar/harrier-0.6b")
# LM Studio / 自定义 API
model = OpenAICompatibleEncoder("http://localhost:1234", "your-model", api_key="...")
```

前置条件：

- 嵌入后端已运行（默认 Ollama：`ollama pull leoipulsar/harrier-0.6b`，或 LM Studio / 兼容 API）
- 可选：`DEEPSEEK_API_KEY` 环境变量（启用 AI 审校）

### 2.2 缓存与路径

| 类别       | 文件                     | 默认位置                       |
| ---------- | ------------------------ | ------------------------------ |
| 嵌入缓存   | `vecs.db` (SQLite)       | `{cache_root}/emb/{entry_id}/` |
| 报告       | `{entry_id}.report.json` | `{cache_root}/reports/`        |
| 修复后原文 | `{entry_id}.source.md`   | 输出目录                       |
| 修复后译文 | `{entry_id}.target.md`   | 输出目录                       |

```python
from dualign import get_cache_root, repair_session_path

get_cache_root()                     # 缓存根目录
repair_session_path(entry_id)        # 报告文件路径
```

### 2.3 核心对齐 API

```python
from dualign import AlignConfig, align, AlignmentResult, op_type_str
```

**AlignConfig**：

| 字段               | 类型   | 默认值 | 说明                        |
| ------------------ | ------ | ------ | --------------------------- |
| `allow_deletions`  | `bool` | `True` | 是否允许 1:0 删除           |
| `allow_insertions` | `bool` | `True` | 是否允许 0:1 插入           |
| `allow_merge`      | `bool` | `True` | 设为 `False` 时退化为纯 1:1 |

**AlignmentResult**：

```python
result = align(src_lines, tgt_lines, src_emb, tgt_emb, config=None, encode_fn=None)
result.all_ops        # [(src_indices, tgt_indices, score), ...]
result.stats          # 统计字典
result.sim_matrix     # 余弦相似度矩阵
```

### 2.4 模型层 API

**AlignmentSnapshot**：

```python
from dualign import AlignmentSnapshot, MISSING

snapshot = AlignmentSnapshot.from_alignment(ops, src_lines, tgt_lines)
snapshot.original_ops          # Tuple of (src_indices, tgt_indices, score)
snapshot.original_src_lines    # Tuple[str, ...]
snapshot.original_tgt_lines    # Tuple[str, ...]
snapshot.src_text(idx)         # 安全获取原文
snapshot.tgt_text(idx)         # 安全获取译文
snapshot.to_dict()             # 序列化
```

`MISSING = "\u27e2MISSING\u27e3"` — 缺失文本占位符。

**RepairAction**：

```python
from dualign import RepairAction
```

| 工厂方法                             | kind                  | data                             |
| ------------------------------------ | --------------------- | -------------------------------- |
| `make_merge(op_index)`               | `merge`               | `{"orig_snaps": [...]}` (跨snap) |
| `make_split(op_index, ...)`          | `split`               | `{"new_src_lines": [...], ...}`  |
| `make_edit(op_index, ...)`           | `edit`                | `{"new_src_lines": [...], ...}`  |
| `make_delete(op_index)`              | `delete`              | `{}`                             |
| `make_ok(op_index)`                  | `ok`                  | `{}`                             |
| `make_flag(op_index, note)`          | `flag`                | `{"note": "..."}`                |
| `make_placeholder_src/tgt(op_index)` | `placeholder_src/tgt` | `{}`                             |

**ChapterState / SnapGroup / AlignedRow**：

```python
from dualign import ChapterState, SnapGroup, AlignedRow
# ChapterState.groups: Tuple[SnapGroup]
# SnapGroup: snap_i, rows: Tuple[AlignedRow]
# AlignedRow: snap_index, sub, init_type, cur_type, src_text, tgt_text, score, orig_score, marker
```

**AiProposal / AiProposalStore**：

```python
from dualign import AiProposal, AiProposalStore

store = AiProposalStore()
store.add(snap_i, action, summary="")
store.accept(snap_i, action)   # → bool
store.reject(snap_i, action)   # → bool
proposals = store.get(snap_i)  # → List[AiProposal]
```

### 2.5 修复服务 API

```python
from dualign import RepairService, RepairState, replay, make_table_view
```

**RepairService**：

- `align_and_repair(src, tgt, model, strategy)` — 一键管线
- `auto_repair(state, strategy, model)` — 自动修复
- `apply_split(state, snap_i, side, model)` — 拆分

**RepairState**：

- `state.apply(action)` → 新 `RepairState`
- `state.undo()` → 新 `RepairState`
- `state.reset()` → 清空所有修复
- `state.current` → `ChapterState`
- `state.repair_log` → `List[RepairAction]`

### 2.6 AI 审校 API

```python
from dualign import AiRepairAgent, ChapterContext, AgentEvent

agent = AiRepairAgent(backend="deepseek", max_turns=10)
ctx = ChapterContext.from_repair_state(repaired_state, chapter_id="ch01")
actions = agent.run(ctx)
```

| 参数          | 类型    | 默认值       | 说明                        |
| ------------- | ------- | ------------ | --------------------------- |
| `backend`     | `str`   | `"deepseek"` | LLM 后端（仅 `"deepseek"`） |
| `temperature` | `float` | `0.0`        | 采样温度                    |
| `max_turns`   | `int`   | `20`         | 最大交互轮数                |
| `thinking`    | `bool`  | `True`       | 启用思考链                  |

### 2.7 CLI 流水线 API

供消费端集成到批处理工作流的原子对齐函数：

```python
from dualign.services.cli_pipeline import align_chapter
```

```python
def align_chapter(
    src_path: str,          # 原文 .md 路径
    tgt_path: str,          # 译文 .md 路径
    repaired_dir: str = "", # report.json 输出目录（默认缓存目录）
    model=None,             # 嵌入模型（None 则自动加载默认配置）
    config=None,            # AlignConfig（默认允许 N:M/1:0/0:1）
    strategy: str = "src",  # 自动修复策略: "src" | "tgt" | "minimal"
    output_dir: str = "",   # 修复后 .md 输出目录（默认同 repaired_dir）
) -> dict
```

返回值结构：

| 键            | 类型 | 说明                                            |
| ------------- | ---- | ----------------------------------------------- |
| `success`     | bool | 是否成功                                        |
| `quality`     | str  | `"reliable"` / `"degraded"` / `"unreliable"`    |
| `rejections`  | list | 质量拒签原因（空列表 = 通过）                   |
| `ops`         | list | 对齐操作列表 `[(src_idx, tgt_idx, score), ...]` |
| `stats`       | dict | 锚点密度、平均分等统计                          |
| `report_path` | str  | 导出的 `report.json` 绝对路径                   |
| `error`       | str  | 仅在 `success=False` 时有，错误描述             |

> 此 API 是为批处理消费端设计的**文件→文件**抽象——一次处理一对文件。
> 内部已包含内容级哈希缓存，文件未变化时自动跳过。
> 三种典型集成模式见 [demo/batch/](../demo/batch/README.md)。

### 2.8 工具函数

```python
from dualign import (
    FilePairMatcher, MatchRule, MatchedPair,  # 批量文件匹配
    repair_session_path, get_report_cache_dir, get_cache_root,
)
from dualign.core import (
    PunctuationHandler, UniversalSplitter,          # 标点分割
    calculate_punctuation_similarity, detect_language_mix,  # 语言检测
)
```

---

## 3. 数据格式规范

### 3.1 report.json 顶层结构

```json
{
    "chapter_id": "130648",
    "created_at": "2026-06-16T13:33:52",
    "src_hash": "sha256...",
    "tgt_hash": "sha256...",
    "quality": { ... },
    "ops": [],
    "stats": {},
    "repair_log": []
}
```

| 字段           | 类型  | 必填 | 说明                    |
| -------------- | ----- | ---- | ----------------------- |
| `chapter_id`   | str   | ✅   | 章节标识                |
| `created_at`   | str   | ✅   | ISO 时间戳              |
| `src_hash`     | str   | ✅   | 原文 SHA256             |
| `tgt_hash`     | str   | ✅   | 译文 SHA256             |
| `quality`      | dict  | ✅   | 质量门控结果            |
| `ops`          | array | ✅   | 对齐操作列表            |
| `stats`        | dict  | ✅   | 对齐统计                |
| `repair_log`   | array | —    | 修复操作列表            |
| `ai_review`    | dict  | —    | AI 审校状态（见 3.5）   |
| `ai_proposals` | dict  | —    | AI 建议记录（GUI 专用） |

### 3.2 quality — 质量门控

| quality         | 含义           | rejections 可能值            |
| --------------- | -------------- | ---------------------------- |
| `ok`            | 正常           | `[]` 或 `["merge_overflow"]` |
| `gap_dominated` | 孤行占比 ≥ 10% | `["gap_dominated"]`          |
| `unreliable`    | 锚点密度 < 60% | `["low_anchor_density"]`     |

```json
{
    "quality": "ok",
    "rejections": ["merge_overflow"],
    "indicators": {
        "anchor_density": 0.45,
        "gap_row_ratio": 0.0,
        "n_overflow_rows": 3,
        "n_src": 60,
        "n_tgt": 60
    }
}
```

### 3.3 ops — 对齐操作

```json
{ "s": [0, 1], "t": [2], "sc": 0.8734 }
```

| 字段 | 类型  | 说明       |
| ---- | ----- | ---------- |
| `s`  | int[] | 原文行索引 |
| `t`  | int[] | 译文行索引 |
| `sc` | float | 余弦相似度 |

### 3.4 repair_log — 修复操作

```json
{
    "op_index": 5,
    "kind": "edit",
    "source": "ai",
    "data": { "new_src_lines": ["..."], "new_tgt_lines": ["..."] },
    "timestamp": "2026-06-16T13:35:00"
}
```

| 字段        | 类型 | 说明                   |
| ----------- | ---- | ---------------------- |
| `op_index`  | int  | 操作的 snap 索引       |
| `kind`      | str  | 操作类型               |
| `source`    | str  | "auto" / "ai" / "user" |
| `data`      | dict | 附加数据               |
| `timestamp` | str  | ISO 时间戳             |

### 3.5 ai_review — AI 审校状态

```json
{
    "status": "completed",
    "note": "",
    "timestamp": "2026-06-18T00:10:00"
}
```

| 字段        | 类型 | 说明                                    |
| ----------- | ---- | --------------------------------------- |
| `status`    | str  | `"completed"` / `"skipped"` / `"error"` |
| `note`      | str  | 备注（跳过原因或错误信息）              |
| `timestamp` | str  | ISO 时间戳                              |

三种状态含义：

- **`completed`** — AI 校订已执行
- **`skipped`** — 无待审异常，跳过（`note`: `"无待审核异常"`）
- **`error`** — AI 校订出错（`note`: 错误信息）

`ai_review` 与 `repair_log` 正交：`repair_log` 记录具体修复操作，`ai_review` 仅记录 AI 是否执行过。无操作（AI 全部 ok）时 `repair_log` 为空但 `ai_review.status = "completed"`。
