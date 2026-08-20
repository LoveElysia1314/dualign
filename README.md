# Dualign

双文档对齐、校订与人工审核工具。

Dualign 为两份自然结构的 Markdown 建立 1:1、N:1、1:M、N:M 和单侧缺失关系。文档 A / 文档 B 是中性位置；如果需要更多语言，按实际需要分别建立文档对。

## 当前工作流

- `report.json` 是唯一持久工作状态，保存初始关系、修订操作、AI 建议、审核结果、正文哈希和生成来源。
- 普通保存与自动保存只更新报告，不修改输入文件。
- 只有用户明确选择“覆写源文档”时，才会把报告中的修订写回两份 Markdown。
- Snap/RepairState 从不可变初始关系重放操作，正文编辑不会让后续操作因行号变化而漂移。
- 旧报告不迁移；重新对齐时可继续复用独立的 SQLite 词向量缓存。
- 等行 Markdown 只作为显式、临时的阅读器兼容制品生成。

完整设计见 [工作报告架构](docs/architecture.md)。

## 安装与启动

```bash
uv sync
uv run dualign
```

环境检查：

```bash
uv run dualign check
uv run dualign models
```

命令行生成报告：

```bash
uv run dualign align -a document-a.md -b document-b.md -o chapter.report.json
```

省略 `-o` 时，报告默认写在文档 A 旁边。该命令不会改写两份文档。

## Demo

GUI 中选择“打开 Demo”，或运行：

```bash
uv run python -m demo.demo_cli
```

每次启动都会先把内置样例复制到独立的系统临时目录。保存报告、自动校订或显式覆写都只影响本次副本，因此 Demo 可以反复运行；终端会打印临时工作区和报告位置。

## Python 集成

```python
from dualign.services.cli_pipeline import align_documents

result = align_documents(
    "document-a.md",
    "document-b.md",
    "chapter.report.json",
)
assert result["success"]
```

为只接受逐行文本的阅读器临时生成两侧内容：

```python
from dualign.services.report_io import materialize_reader_rows

rows_a, rows_b = materialize_reader_rows(
    "chapter.report.json",
    "document-a.md",
    "document-b.md",
)
```

## 开发

```bash
uv run pytest -q
```

许可证见 [LICENSE](LICENSE)。
