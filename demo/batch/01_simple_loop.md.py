"""模式一：最简串行批处理。

把 file_pairs 替换为真实路径即可。输出是 *.report.json；
两个输入文档不会被改写，也不会隐式生成等行 Markdown。
"""

from pathlib import Path

from dualign.services.cli_pipeline import align_documents

file_pairs = [
    ("data/ch01.zh.md", "data/ch01.en.md"),
    ("data/ch02.zh.md", "data/ch02.en.md"),
    ("data/ch03.zh.md", "data/ch03.en.md"),
]
output_dir = Path("output/alignments")
output_dir.mkdir(parents=True, exist_ok=True)

for document_a, document_b in file_pairs:
    pair_name = f"{Path(document_a).stem}__{Path(document_b).stem}.report.json"
    result = align_documents(
        document_a_path=document_a,
        document_b_path=document_b,
        report_path=str(output_dir / pair_name),
    )
    status = "✓" if result["success"] else "✗"
    detail = result.get("report_path", result.get("error", ""))
    print(f"{status} {Path(document_a).name} ↔ {Path(document_b).name}: {detail}")
