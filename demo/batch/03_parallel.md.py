"""
模式三：线程池并行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

适用场景：大量文件对（200+ 对），或单次对齐延迟较高的场景
         （如云端 API），需要控制并发数。
教学目的：展示消费端如何用 concurrent.futures 控制并发，
          以及怎样在编排层做结果聚合。

前置条件:
  - 同模式一、二
  - 理解 Python 的 ThreadPoolExecutor

┌─────────────────────────────────────────────────────────────┐
│  为什么是线程池而不是协程？                                    │
│  - align_chapter 是同步 I/O 密集型（HTTP 调用 Ollama API）   │
│  - Python 的 GIL 在 I/O 等待时自动释放，不阻塞其他线程       │
│  - ThreadPoolExecutor 是最简单可靠的并发原语，无需额外依赖   │
│                                                            │
│  为什么不是 multiprocessing？                                │
│  - 多进程带来序列化和 IPC 开销                              │
│  - 对齐本身是 I/O-bound（等 HTTP），不是 CPU-bound          │
└─────────────────────────────────────────────────────────────┘

BilingualRanobeReader 实测：
  max_workers=6 在对齐阶段能稳定跑满 Ollama 的批处理能力，
  同时不给本地推理带来过大压力。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from dualign.services.cli_pipeline import align_chapter

# ═══════════════════════════════════════════════════════════════
# 消费端自行定义的结果结构
# ═══════════════════════════════════════════════════════════════


@dataclass
class ParallelBatchResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    details: list[dict] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 并行批量对齐
# ═══════════════════════════════════════════════════════════════


def batch_align_parallel(
    file_pairs: list[tuple[str, str]],
    output_dir: str = "output/",
    strategy: str = "src",
    max_workers: int = 4,  # ← 并发控制的关键参数
) -> ParallelBatchResult:
    """并行批量对齐 — ThreadPoolExecutor 版。

    消费端决定并发数，Dualign 不碰线程管理。
    """

    def _align_one(pair: tuple[str, str]) -> dict:
        """单对对齐的 worker 函数。"""
        src, tgt = pair
        r = align_chapter(
            src_path=src,
            tgt_path=tgt,
            output_dir=output_dir,
            strategy=strategy,
        )
        return {"src": src, "tgt": tgt, **r}

    result = ParallelBatchResult(total=len(file_pairs))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # 提交所有任务
        future_map = {pool.submit(_align_one, pair): pair for pair in file_pairs}

        # 逐条收集结果（as_completed 会在每个任务完成时立即返回）
        for future in as_completed(future_map):
            r = future.result()
            if r.get("success"):
                result.succeeded += 1
            else:
                result.failed += 1
            result.details.append(r)

    return result


# ═══════════════════════════════════════════════════════════════
# 使用示例
# ═══════════════════════════════════════════════════════════════

# ── 场景 A：保守并发（本地 Ollama）──
# result = batch_align_parallel(
#     file_pairs,
#     max_workers=2,
# )

# ── 场景 B：高并发（云端 API）──
# result = batch_align_parallel(
#     file_pairs,
#     max_workers=8,
# )

# ── 场景 C：BilingualRanobeReader 风格——DAG 中单一阶段 ──
# def align_stage(chapters, max_workers=6):
#     """管线中的对齐阶段，配合爬取/翻译/阅读器组合。"""
#     pairs = [
#         (build_chapter_path("raw", "source", ch),
#          build_chapter_path("raw", "target", ch))
#         for ch in chapters
#     ]
#     return batch_align_parallel(pairs, max_workers=max_workers)

# 关于并发数选择的进一步讨论见 README.md "常见问题" 章节。
