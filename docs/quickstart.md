# 快速开始

## 安装

```bash
uv sync
uv run dualign check
```

默认启动图形界面：

```bash
uv run dualign
```

在界面中打开文档 A 和文档 B。初次对齐会生成 `report.json`；后续校订、AI 建议和审核操作都会自动保存到同一报告。普通保存不会改写 Markdown。

只有确定要写入当前校订时，才使用“文件 → 固化修改”。可先在“固化范围”选择文档 A/B 的合并、拆分、校订和双侧文本对删除，或使用预设；系统会先显示差异，再同时更新两份文档和报告。占位不会被写入正文。

## 体验 Demo

“打开 Demo”会为每次运行创建新的临时文档对。即使在 Demo 中执行“固化修改”，内置样例也不会被修改，下次打开仍会从原始样例开始。

## 命令行

```bash
uv run dualign align \
  -a document-a.md \
  -b document-b.md \
  -o chapter.report.json
```

输出只有工作报告，不会生成配对 Markdown。

固化命令默认只预览，追加 `--apply` 才会写入：

```bash
uv run dualign solidify \
  -a document-a.md -b document-b.md -r chapter.report.json \
  --preset line-aligned
```

配置文件支持 JSON 或 TOML。例如 `policy.toml`：

```toml
[solidify]
preset = "edits"
include = ["merge_b", "split_b", "delete_pair"]
exclude = ["edit_a"]
```

## 集成阅读器

调用 `dualign.services.report_io.materialize_reader_rows()` 临时获得等长文本行。不要把返回内容作为新的权威正文长期保存。
