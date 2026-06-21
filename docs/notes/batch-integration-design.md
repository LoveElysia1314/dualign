# 批处理集成论证与 Demo 设计

> 暂存设计文档 — 论证 Dualign 作为批处理管线组件的可行性、现有实现分析、以及一个可配置示例 Demo 的设计方案。

---

## 1. 研究背景

### 1.1 消费端实测

以 [BilingualRanobeReader](https://github.com/LoveElysia1314/BilingualRanobeReader) 为典型消费端，其管线组织方式如下：

```
PipelineEngine (DAG 调度)
  ├── crawl (Semaphore=3)
  ├── translate (Semaphore=20)
  ├── align (Semaphore=6)        ← Dualign 介入点
  │    └── 逐章调用 align_chapter()
  ├── ai_repair (Semaphore=20)
  └── viewer (Semaphore=1)
```

关键发现：

| 方面         | 实现方式                                                                    |
| ------------ | --------------------------------------------------------------------------- |
| **对齐调用** | `from dualign.services.cli_pipeline import align_chapter` — 每章一次调用    |
| **批量编排** | 消费端自行实现：`batch_align_all_volumes()` 遍历 catalog → 逐章调用         |
| **结果聚合** | 消费端自行实现：`BatchAlignResult` dataclass + 回调进度报告                 |
| **缓存策略** | 消费端索引 `report.json` 的存在性判断是否跳过；Dualign 内部有内容级哈希缓存 |
| **错误处理** | 消费端 try/except 包裹每章，失败计入 `BatchAlignResult.errors`              |
| **并发控制** | 消费端 ThreadPoolExecutor(6) 控制对齐并发数                                 |

结论：**批量编排完全是消费端职责**。Dualign 提供的是"单次对齐"的原子能力。

### 1.2 Dualign 现有 CLI 能力

```
python -m dualign align --src A.md --tgt B.md --out DIR
python -m dualign auto  --src A.md --tgt B.md --out DIR   (align + repair)
```

- `align_chapter()` — 单体对齐，接受 `(src_path, tgt_path, ...)` 返回 dict
- `BatchDiscoveryDialog` — GUI 文件对发现，**无 CLI 等价物**
- **无** 目录级或文件对列表级的批处理循环
- **无** 统一的 `BatchResult` 聚合结构
- **无** 回调式的进度报告机制

---

## 2. 论：Dualign 需要适配批处理吗？

### 2.1 不应做的（Why NOT）

| 理由             | 说明                                                                                                                                |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **编排范式过多** | 消费端的工作流千差万别：有的用 DAG（如 PipelineEngine）、有的用 Makefile、有的用 Python 脚本手写 for 循环。Dualign 不可能适配所有。 |
| **保持原子性**   | `align_chapter()` 是一个定义清晰的原子操作（文件→文件），这是正确的抽象层级。向上堆叠编排逻辑只会增加维护成本。                     |
| **单一职责**     | Dualign 的核心价值在对齐算法和 GUI 审校工作台，不是编排引擎。                                                                       |

### 2.2 可以做的（Why YES）

| 可以做的事                       | 价值                                                                    |
| -------------------------------- | ----------------------------------------------------------------------- |
| 提供一个**可配置的示例 Demo**    | 降低消费端集成门槛，让新用户 5 分钟理解"怎么把我的文件列表喂给 Dualign" |
| 在文档中**明确批处理工作流思路** | 让用户意识到"Dualign 可以嵌入到我的管线中"，而不是只能在 GUI 里手动操作 |
| 提供**纯函数的 Python API 示例** | 展示如何在 for 循环、线程池、asyncio 等不同范式中调用                   |

---

## 3. 设计方案：可配置示例 Demo

### 3.1 定位

- **不引入新依赖**（不需要 anyio、celery 等）
- **不修改核心库**（完全独立于 `src/dualign/`）
- **不可运行**（无运行时依赖，仅供阅读教学）
- **三段式结构**：最简循环 → 带进度 → 多级并发

### 3.2 文件位置

```
demo/
  batch/
    README.md              # 批处理集成思路 + 三种模式的教学文档
    01_simple_loop.md.py   # 模式一：最简 for 循环
    02_with_progress.md.py # 模式二：带进度回调
    03_parallel.md.py      # 模式三：线程池并行（控制并发数 + 结果聚合）
```

> 文件名用 `.md.py` 后缀：作为 Markdown 可直接阅读，作为 Python 可被语法高亮。

### 3.3 三段式内容设计

#### 模式一：最简 for 循环 — `01_simple_loop.md.py`

**教学目的**：让用户理解 Dualign 对齐的原子 API。

```python
"""
模式一：最简 for 循环
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
适用场景：少量文件对（< 10 对），串行执行，不需进度报告。
教学目的：展示 Dualign 的最简集成方式——只需两样东西：(1) 文件对列表, (2) for 循环。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

前置条件:
  - Ollama 运行中，已拉取 leoipulsar/harrier-0.6b
  - pip install dualign

数据结构假设:
  file_pairs = [
    ("ch1.source.md", "ch1.target.md"),
    ("ch2.source.md", "ch2.target.md"),
    ...
  ]
"""

# ── 1. 导入 ──
from dualign.services.cli_pipeline import align_chapter

# ── 2. 准备文件对列表 ──
# 这是消费端需要自行准备的核心数据：原文路径 ↔ 译文路径
file_pairs = [
    ("data/ch1.source.md", "data/ch1.target.md"),
    ("data/ch2.source.md", "data/ch2.target.md"),
]

# ── 3. 串行对齐 ──
# align_chapter 是 Dualign 提供的原子 API：
#   输入：原文路径、译文路径、输出目录、修复策略
#   输出：{"success": bool, "ops": [...], "quality": str, ...}
for src, tgt in file_pairs:
    result = align_chapter(
        src_path=src,
        tgt_path=tgt,
        output_dir="output/repaired/",
        strategy="src",          # src|tgt|minimal
    )
    print(f"{src} → {'✓' if result['success'] else '✗'} quality={result.get('quality', '?')}")
```

**需要强调的**：

- Dualign 的 API 是**纯同步**的 — 消费端自行决定并发范式
- `align_chapter` 内部已包含缓存管理（hash 检测到文件未变化时复用）
- 输出产物：`{output_dir}/{entry_id}.report.json` + 修复后 `.md`

---

#### 模式二：带进度回调 — `02_with_progress.md.py`

**教学目的**：让用户了解可在外围封装回调机制获取进度。

```python
"""
模式二：带进度回调
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
适用场景：中等数量文件对（10-200 对），需要进度报告以支持 GUI 进度条或 CLI 输出。
教学目的：展示如何在外围封装进度回调，无需 Dualign 提供任何回调机制。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from dualign.services.cli_pipeline import align_chapter


# ── 消费端自行定义的结果聚合结构 ──
@dataclass
class BatchAlignResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)


def batch_align(
    file_pairs: list[tuple[str, str]],
    output_dir: str = "output/",
    strategy: str = "src",
    *,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> BatchAlignResult:
    """通用的批量对齐函数——消费端自行实现。

    Args:
        file_pairs: [(src_path, tgt_path), ...]
        on_progress: 进度回调(index, total, status_msg)
    """
    result = BatchAlignResult(total=len(file_pairs))

    for i, (src, tgt) in enumerate(file_pairs):
        label = f"[{i+1}/{len(file_pairs)}] {src}"

        # 可选：跳过已对齐（检查 report.json 存在性）
        # if is_already_aligned(...): result.skipped += 1; continue

        if on_progress:
            on_progress(i, len(file_pairs), f"正在对齐 {src}")

        try:
            r = align_chapter(
                src_path=src,
                tgt_path=tgt,
                output_dir=output_dir,
                strategy=strategy,
            )
            if r.get("success"):
                result.succeeded += 1
            else:
                result.failed += 1
                result.errors.append({"src": src, "error": r.get("error", "unknown")})
        except Exception as e:
            result.failed += 1
            result.errors.append({"src": src, "error": str(e)})

        if on_progress:
            status = "✓" if r.get("success") else "✗" if not r.get("success") else "?"
            on_progress(i + 1, len(file_pairs), f"{src} → {status}")

    return result


# ── 使用示例：GUI 进度条模式 ──
# def on_gui_progress(current, total, msg):
#     window.progress_bar.setValue(int(current / total * 100))
#     window.status_label.setText(msg)
#
# result = batch_align(file_pairs, on_progress=on_gui_progress)

# ── 使用示例：CLI 进度输出模式 ──
# result = batch_align(file_pairs, on_progress=lambda c, t, m: print(f"\r{m}", end=""))
# print(f"\n完成: {result.succeeded}/{result.total}")
```

---

#### 模式三：线程池并行 — `03_parallel.md.py`

**教学目的**：展示如何在消费端实现受控并发（对应 BilingualRanobeReader 的实际做法）。

```python
"""
模式三：线程池并行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
适用场景：大量文件对（200+ 对），需要控制并发数避免压垮 Ollama 或 API。
教学目的：展示消费端如何用 concurrent.futures 控制并发，以及怎样做结果聚合。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dualign.services.cli_pipeline import align_chapter


def batch_align_parallel(
    file_pairs: list[tuple[str, str]],
    output_dir: str = "output/",
    strategy: str = "src",
    max_workers: int = 4,          # ← 并发控制的关键参数
) -> dict:
    """并行批量对齐 — 线程池版。

    为什么是线程池而不是协程？
    - align_chapter 是同步 I/O 密集型（HTTP 调用 Ollama）
    - Python 的 GIL 在 I/O 等待时不阻塞
    - ThreadPoolExecutor 是最简单可靠的并发原语
    """

    def _align_one(src_tgt: tuple[str, str]) -> dict:
        src, tgt = src_tgt
        r = align_chapter(
            src_path=src, tgt_path=tgt,
            output_dir=output_dir, strategy=strategy,
        )
        return {"src": src, **r}

    result = {"succeeded": 0, "failed": 0, "details": []}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_align_one, pair): pair for pair in file_pairs}

        for future in as_completed(futures):
            r = future.result()
            if r.get("success"):
                result["succeeded"] += 1
            else:
                result["failed"] += 1
            result["details"].append(r)

    return result


# ── 并发数选择建议 ──
# max_workers  | 瓶颈               | 推荐场景
# 1            | Ollama 单线程推理   | 本地模型，无 GPU
# 2-4          | Ollama 多批推理     | 本地 GPU / 中低延迟 API
# 6-10         | API 速率限制        | DeepSeek / 云端 API
# >10          | I/O 带宽            | 不建议，收益递减

# ── BilingualRanobeReader 真实场景 ──
# 消费端使用 Semaphore(6) 控制对齐并发数，
# 配合 ThreadPoolExecutor(50) 整体管线（翻译/对齐混合）。
# 这是由于翻译阶段依赖 DeepSeek API（高延迟），需要更高并发。
```

### 3.4 配套教学文档 `demo/batch/README.md`

```
# 将 Dualign 接入批处理工作流

## 核心理念

Dualign 的对齐引擎是**纯函数级的原子能力**。它不关心你的文件从哪里来、
有多少对、要不要并发——这些是**消费端的职责**。

```

你的文件对列表 ──→ [你的 for 循环 / 线程池 / DAG] ──→ align_chapter() × N ──→ 修复后的文件

````

## 三种集成模式速览

| 模式 | 文件 | 适用场景 | 复杂度 |
|------|------|----------|--------|
| 最简串行 | `01_simple_loop.md.py` | <10 对，快速尝鲜 | ★☆☆ |
| 进度报告 | `02_with_progress.md.py` | 10-200 对，GUI/CLI 需要进度条 | ★★☆ |
| 并行加速 | `03_parallel.md.py` | 200+ 对，需要控制并发 | ★★★ |

## 常见问题

### Q: Dualign 为什么不内置批处理循环？

因为编排范式太多。有 for 循环、Makefile、DAG 引擎、消息队列……
Dualign 选择保持原子性，让消费端按自己的基础设施选择合适的编排方式。

### Q: 如何跳过已对齐的章节？

代码中检查 `report.json` 存在性即可：

```python
import os
report_path = f"output/{entry_id}.report.json"
if os.path.isfile(report_path):
    print(f"跳过 {entry_id} — 已对齐")
    continue
````

Dualign 内部还有内容级哈希缓存：即使 `report.json` 存在，若源文件内容变化也会自动重新对齐。

### Q: 如何获取对齐质量信息？

`align_chapter()` 返回的 dict 包含 `quality` 键：

```python
{
    "success": True,
    "quality": "reliable",    # reliable | degraded | unreliable
    "rejections": [],          # 如果 quality 不是 reliable，这里列出原因
    "ops": [...],              # 对齐操作列表
}
```

### Q: 如何处理对齐失败？

在 for 循环中用 try/except 包裹即可，失败计入自己的错误列表，不影响后续章节。

### Q: 如何在自己的 GUI 中嵌入 Dualign？

参考 `demo/batch/02_with_progress.md.py` 中的回调模式：

1. 在 GUI 线程中启动一个后台线程运行 `batch_align()`
2. 通过 `on_progress` 回调将进度信号发送到 GUI 线程
3. 完成后在主线程更新 UI

如果你使用 PySide6，可以用 `QThread` + `Signal` 替代回调。

````

---

## 4. 文档更新建议

建议在以下位置加入批处理工作流的简要说明：

### 4.1 `README.md` — 功能全景部分追加

在 `docs/README.md` 的功能分类中增加一节：

```markdown
### 🔄 批处理工作流集成

Dualign 的对齐引擎可无缝嵌入你的批处理管线：

- **原子 API**：`align_chapter()` — 一对文件 → 对齐 → 自动修复 → 导出
- **无状态设计**：不持有全局状态，适合 for 循环 / 线程池 / DAG
- **幂等缓存**：内容哈希驱动，重复执行自动跳过
- **质量分级**：输出 `reliable / degraded / unreliable` 三级质量标签

→ 详情与代码示例见 [批处理 Demo](demo/batch/README.md)
````

### 4.2 `docs/reference.md` — API 参考中补充

在 `2. Python API 参考` 部分增加 `align_chapter()` 的签名文档和返回值结构。

---

## 5. 结论总结

| 问题                                       | 结论                                                                                                                                      |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 批处理逻辑是消费端实现的还是本项目支持的？ | **完全由消费端实现**。Dualign 提供原子对齐 API，消费端负责遍历、并发、结果聚合。                                                          |
| Dualign 应该内置批处理吗？                 | **不应该**。编排范式过多，保持原子性才是正确的抽象层级。                                                                                  |
| 需要做什么？                               | (1) 提供 `demo/batch/` 三段式教学示例 (2) 在文档中补一笔"批处理工作流集成"的分类说明 (3) 完善 API 文档中 `align_chapter()` 的返回值说明。 |
| 三段式示例能覆盖哪些消费端？               | 最简 for 循环 → 任何语言/框架；回调进度 → GUI 应用；线程池并行 → 批量生产管线。                                                           |
