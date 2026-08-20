"""模式二：由消费端封装进度回调和幂等跳过。"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from dualign.services.cli_pipeline import align_documents


@dataclass
class BatchAlignResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[dict] = field(default_factory=list)
    duration: float = 0.0


def batch_align(
    file_pairs: list[tuple[str, str]],
    output_dir: str = "output/alignments",
    *,
    skip_existing: bool = False,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> BatchAlignResult:
    """逐对写入工作报告；不改写文档 A 或文档 B。"""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    result = BatchAlignResult(total=len(file_pairs))
    started = time.time()

    for index, (document_a, document_b) in enumerate(file_pairs, start=1):
        label = f"[{index}/{len(file_pairs)}] {Path(document_a).name}"
        pair_name = f"{Path(document_a).stem}__{Path(document_b).stem}.report.json"
        report_path = destination / pair_name

        if skip_existing and report_path.is_file():
            result.skipped += 1
            if on_progress:
                on_progress(index, len(file_pairs), f"{label} → 已跳过")
            continue

        if on_progress:
            on_progress(index - 1, len(file_pairs), f"{label} → 对齐中…")

        item: dict = {}
        try:
            item = align_documents(
                document_a_path=document_a,
                document_b_path=document_b,
                report_path=str(report_path),
            )
            if item.get("success"):
                result.succeeded += 1
            else:
                result.failed += 1
                result.errors.append(
                    {"document_a": document_a, "error": item.get("error", "unknown")}
                )
        except Exception as exc:
            result.failed += 1
            result.errors.append({"document_a": document_a, "error": str(exc)})

        if on_progress:
            status = "完成" if item.get("success") else "失败"
            on_progress(index, len(file_pairs), f"{label} → {status}")

    result.duration = time.time() - started
    return result


# CLI：
# result = batch_align(file_pairs, on_progress=lambda c, t, m: print(c, t, m))
#
# GUI：在后台线程调用 batch_align，并把 on_progress 转发为 Qt Signal。
