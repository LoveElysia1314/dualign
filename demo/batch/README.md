# 将 Dualign 接入批处理工作流

> 教学文档 — 展示如何将 Dualign 的对齐引擎嵌入你自己的批处理管线。

---

## 核心理念

Dualign 的对齐引擎是**纯函数级的原子能力**。它不关心你的文件从哪里来、
有多少对、要不要并发——这些是**消费端的职责**。

```
你的文件对列表 ──→ [你的 for 循环 / 线程池 / DAG] ──→ align_chapter() × N ──→ 修复后的文件
```

Dualign 提供的是每次处理**一对文件**的原子 API `align_chapter()`，消费端负责：

1. **准备文件对列表** — 谁跟谁对齐
2. **选择编排方式** — 串行、并行还是 DAG
3. **聚合结果** — 成功/失败统计
4. **处理错误** — 失败不影响后续

---

## 三种集成模式速览

| 模式     | 文件                                               | 适用场景                      | 复杂度 |
| -------- | -------------------------------------------------- | ----------------------------- | ------ |
| 最简串行 | [`01_simple_loop.md.py`](01_simple_loop.md.py)     | < 10 对，快速尝鲜             | ★☆☆    |
| 进度报告 | [`02_with_progress.md.py`](02_with_progress.md.py) | 10–200 对，GUI/CLI 需要进度条 | ★★☆    |
| 并行加速 | [`03_parallel.md.py`](03_parallel.md.py)           | 200+ 对，需要控制并发数       | ★★★    |

---

## 前置条件

这三种模式都依赖相同的运行环境：

- **Ollama** 运行中，已拉取 `leoipulsar/harrier-0.6b`
- 已安装 Dualign（`pip install -e .`）
- 一对或多对 `.source.md` / `.target.md` 文件

---

## align_chapter() 签名速查

`align_chapter` 是三种模式共同依赖的核心 API，来自 `dualign.services.cli_pipeline`：

```python
def align_chapter(
    src_path: str,          # 原文文件路径
    tgt_path: str,          # 译文文件路径
    repaired_dir: str = "", # report.json 输出目录（默认缓存目录）
    model=None,             # 嵌入模型（None 则自动加载）
    config=None,            # AlignConfig
    strategy: str = "src",  # 修复策略: "src" / "tgt" / "minimal"
    output_dir: str = "",   # 修复后 .md 输出目录（默认同 repaired_dir）
) -> dict
```

返回值结构：

```python
{
    "success": bool,         # 是否成功
    "ops": [...],            # 对齐操作列表
    "quality": str,          # "reliable" | "degraded" | "unreliable"
    "rejections": [...],     # 质量拒签原因（空列表表示通过）
    "stats": {...},          # 统计信息
    "report_path": str,      # report.json 路径
    "error": str,            # 仅失败时有
}
```

---

## 常见问题

### Q: Dualign 为什么不内置批处理循环？

因为编排范式太多——有 for 循环、Makefile、DAG 引擎、消息队列……
Dualign 选择保持原子性，消费端按自己的基础设施选择合适的编排方式。

### Q: 如何跳过已对齐的章节？

Dualign 内部已有内容级哈希缓存（`report.json` 中的 `src_hash` / `tgt_hash`）。
如果文件无变化，重复调用会自动复用缓存。消费端也可在外部检查 `report.json` 存在性做快速跳过。

### Q: 如何处理对齐失败？

在 for 循环中用 try/except 包裹 `align_chapter()`，失败计入自己的错误列表即可。
一次失败不影响后续章节——这就是消费端编排的核心优势。

### Q: 如何在自己的 GUI 中嵌入 Dualign？

参考 `02_with_progress.md.py` 中的回调模式：

1. 在 GUI 线程中启动一个后台线程运行批量对齐
2. 通过 `on_progress` 回调将进度信号发送到 GUI 线程
3. 完成后在主线程更新 UI

如果使用 PySide6，可以用 `QThread` + `Signal` 替代回调。

### Q: 如何控制并发数？

参考 `03_parallel.md.py`。关键参数 `max_workers`：

| max_workers | 瓶颈              | 推荐场景                |
| ----------- | ----------------- | ----------------------- |
| 1           | Ollama 单线程推理 | 本地模型，无 GPU        |
| 2–4         | Ollama 多批推理   | 本地 GPU / 中低延迟 API |
| 6–10        | API 速率限制      | DeepSeek / 云端 API     |
| > 10        | I/O 带宽          | 不推荐，收益递减        |
