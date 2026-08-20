"""模式三：由消费端用线程池并行生成工作报告。"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from dualign.services.cli_pipeline import align_documents


@dataclass
class ParallelBatchResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    details: list[dict] = field(default_factory=list)


def batch_align_parallel(
    file_pairs: list[tuple[str, str]],
    output_dir: str = "output/alignments",
    max_workers: int = 4,
) -> ParallelBatchResult:
    """并行处理互不相关的文档对；调用方负责控制服务端并发。"""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    def align_one(pair: tuple[str, str]) -> dict:
        document_a, document_b = pair
        pair_name = f"{Path(document_a).stem}__{Path(document_b).stem}.report.json"
        item = align_documents(
            document_a_path=document_a,
            document_b_path=document_b,
            report_path=str(destination / pair_name),
        )
        return {"document_a": document_a, "document_b": document_b, **item}

    result = ParallelBatchResult(total=len(file_pairs))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(align_one, pair): pair for pair in file_pairs}
        for future in as_completed(futures):
            try:
                item = future.result()
            except Exception as exc:
                document_a, document_b = futures[future]
                item = {
                    "success": False,
                    "document_a": document_a,
                    "document_b": document_b,
                    "error": str(exc),
                }
            result.succeeded += int(bool(item.get("success")))
            result.failed += int(not item.get("success"))
            result.details.append(item)
    return result


# 本地推理通常从 max_workers=2 开始；云端 API 需遵守提供方速率限制。
