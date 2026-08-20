# 开发参考

## `align_documents`

```python
from dualign.services.cli_pipeline import align_documents

result = align_documents(
    document_a_path,
    document_b_path,
    report_path,
    model=None,
    config=None,
)
```

成功结果含 `success`、`ops`、`stats`、`quality` 和 `report_path`。缓存命中要求两份文档哈希以及模型、算法、配置来源都完全一致。

## 报告

报告顶层 `format` 固定为 `dualign-report`。关键字段：

- `documents.a/b.sha256`：规范化正文哈希；
- `ops`：不可变初始关系，`s` / `t` 是两侧原始块索引；
- `snapshot_fingerprint`：正文、关系、切分方法和来源的规范指纹；
- `repair_log`：以初始关系编号为锚点的操作序列；
- `ai_proposals` / `ai_review` / `scores`：审校状态；
- `history`：覆写源文档时归档的上一轮操作。

`load_report()` 只接受当前格式。旧 JSON 应删除并重新生成。

## 重放与阅读器物化

```python
from dualign.services.report_io import (
    load_report,
    repair_state_from_report,
    materialize_reader_rows,
)
```

重放前必须验证源文档哈希。`materialize_reader_rows()` 返回两组等长字符串，仅用于兼容逐行阅读器。

## 写回

GUI 的“覆写源文档”使用 `pair_save` 的事务实现。写回完成后，报告按新正文建立快照，旧 `repair_log` 进入 `history`，当前操作清空。程序不创建 `.bak`。

详细约束见 [工作报告架构](architecture.md)。
