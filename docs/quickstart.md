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

只有确定要固化当前校订时，才使用“文件 → 覆写源文档”。系统会先显示差异，再同时更新两份源文档和报告。

## 体验 Demo

“打开 Demo”会为每次运行创建新的临时文档对。即使在 Demo 中执行“覆写源文档”，内置样例也不会被修改，下次打开仍会从原始样例开始。

## 命令行

```bash
uv run dualign align \
  -a document-a.md \
  -b document-b.md \
  -o chapter.report.json
```

输出只有工作报告，不会生成配对 Markdown。

## 集成阅读器

调用 `dualign.services.report_io.materialize_reader_rows()` 临时获得等长文本行。不要把返回内容作为新的权威正文长期保存。
