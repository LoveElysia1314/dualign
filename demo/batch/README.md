# 批处理集成

`align_documents()` 一次处理一个文档对并写入 `report.json`。批次发现、并发、重试和聚合由调用方负责。

```python
from dualign.services.cli_pipeline import align_documents

result = align_documents(
    document_a_path="chapter.zh.md",
    document_b_path="chapter.en.md",
    report_path="chapter.report.json",
)
```

成功结果包含 `success`、`report_path`、`ops`、`stats` 和 `quality`。函数会验证正文哈希与完整生成来源，安全命中已有报告；不会改写正文或生成持久的等行副本。

- `01_simple_loop.md.py`：串行处理少量文档对。
- `02_with_progress.md.py`：封装进度与错误聚合。
- `03_parallel.md.py`：受控并发；并发数应服从嵌入服务容量和限流。
