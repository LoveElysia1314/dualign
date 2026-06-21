# Dualign ![version](https://img.shields.io/badge/version-0.7.0-blue)

> **双语平行文档对齐与 AI 辅助校验工具**

Dualign 是一款面向**行级（段落级）双语平行语料**的智能对齐与审校系统。它将原文与译文精确对齐到行级别，自动修复结构性错位，并通过大语言模型辅助语义审校，最终在交互式 GUI 工作台中完成人工终审。

![欢迎页 — 环境检测与快速入口](assets/images/01-welcome-page.png)
_启动欢迎页：包含程序图标、标题、功能定位。卡片显示嵌入模型和 AI 模型运行状态_

> 如果你曾因翻译工具的输出行数不一致而苦恼，或者手动调整双语对照文本耗费数小时——Dualign 就是为你准备的。

---

## 痛点与解法

| 你的痛点                      | Dualign 的解法                                 |
| ----------------------------- | ---------------------------------------------- |
| 翻译 API 输出行数和原文对不上 | 嵌入向量 + 动态规划自动对齐，支持任意 N:1、1:M |
| 原文有内容但译文缺失（漏译）  | 自动检测 N:0 缺失，标记占位符或删除            |
| 译文有内容但原文没有（多译）  | 自动检测 0:M 多余，标记为删除                  |
| 自动修复不放心，需要人工确认  | GUI 工作台逐行审校，支持合并/拆分/编辑/标记    |
| 改错了想撤回                  | 任意操作可撤销/恢复，支持到最近 50 步          |
| 几十个文件要批量对齐          | 批量发现 + 一键修复 + 导出，流程化处理         |

---

## 快速安装

```bash
# 克隆并进入目录
git clone <repo-url>
cd dualign

# 创建虚拟环境（推荐）
python -m venv venv
venv\Scripts\activate     # Windows
source venv/bin/activate  # macOS / Linux

# 安装
pip install -e .
```

**依赖一览**：

| 依赖                          | 用途         | 安装方式 | 必需？        |
| ----------------------------- | ------------ | -------- | ------------- |
| Ollama / LM Studio / 兼容 API | 句子嵌入编码 | 外部服务 | ✅ 必须选其一 |
| Python ≥ 3.10                 | 运行环境     | pip      | ✅ 必须       |
| DeepSeek API Key              | AI 语义审校  | 环境变量 | ❌ 可选       |
| PySide6 ≥ 6.5                 | GUI 工作台   | 核心依赖 | ✅ 已包含     |

> **默认嵌入后端为 Ollama**，也支持 LM Studio 和任意 OpenAI 兼容 API。详见 [quickstart.md](quickstart.md#嵌入后端配置)。

**Windows 用户也可直接下载安装包或便携版**（免 Python 环境）：参见 [GitHub Releases](https://github.com/LoveElysia1314/Dualign/releases)。

---

## 2 条核心命令

```bash
# CLI 一键对齐
dualign align -s 原文.md -t 译文.md -o output/

# GUI 交互式工作台
dualign # 或 dualign gui
```

![校订模式工作台](assets/images/06-edit-anomalies.png)
_校订模式核心工作台：左侧审校面板（筛选/操作/AI审校），中央7列表格展示异常文本对_

---

## 一句话架构

```text
输入原文+译文 → [L1 嵌入对齐] → [L2 规则修复] → [L3 AI 审校] → [L4 GUI 终审] → 输出
```

四层流水线，每层各司其职。详见 [reference.md](reference.md#1-技术架构)。

---

### 🔄 批处理工作流集成

Dualign 的对齐引擎可无缝嵌入你的批处理管线：

- **原子 API**：`align_chapter()` — 一对文件 → 对齐 → 自动修复 → 导出
- **无状态设计**：不持有全局状态，适合 for 循环 / 线程池 / DAG
- **幂等缓存**：内容哈希驱动，重复执行自动跳过
- **质量分级**：输出 `reliable / degraded / unreliable` 三级质量标签

→ 详情与代码示例见 [批处理 Demo](../demo/batch/README.md)

---

## 文档地图

| 文档                                   | 适合谁           | 内容                                  |
| -------------------------------------- | ---------------- | ------------------------------------- |
| [batch/](../demo/batch/README.md)      | **集成开发者**   | 批处理工作流集成教学 + 三段式代码示例 |
| [quickstart.md](quickstart.md)         | **所有新用户**   | 5 分钟从安装到第一次对齐              |
| [user-guide.md](user-guide.md)         | **GUI 用户**     | 工作台完整操作指南                    |
| [reference.md](reference.md)           | **集成开发者**   | Python API、数据格式、配置项          |
| [algorithm.md](algorithm.md)           | **算法贡献者**   | Phase 1→5 对齐算法设计                |
| [development.md](development.md)       | **贡献者**       | 环境搭建、测试、自定义模型            |
| [ai-agent-guide.md](ai-agent-guide.md) | **Agent 编写者** | AI 审校代理的设计与提示词             |
| [faq.md](faq.md)                       | **所有人**       | 常见问题与故障排除                    |
| [CHANGELOG.md](CHANGELOG.md)           | **所有人**       | 版本历史与破坏性变更                  |

---

## 适用场景

| 场景                     | 推荐   | 说明                       |
| ------------------------ | ------ | -------------------------- |
| 轻小说/网文中→英平行阅读 | ⭐⭐⭐ | 最初为此设计               |
| 翻译质量检查             | ⭐⭐⭐ | 找出缺失、多余、错位       |
| 双语字幕对齐             | ⭐⭐⭐ | 行级对齐，语言无关         |
| 平行语料库构建           | ⭐⭐⭐ | 批量处理 + 质量筛选        |
| 双语合同/法律文档对照    | ⭐⭐   | 精确到行级                 |
| 实时翻译                 | ❌     | 非实时系统                 |
| 词级/短语级对齐          | ❌     | 仅行级（段落级）           |
| 非平行文档               | ❌     | 需要原文和译文大致结构对应 |

---

_Dualign 仍在积极开发中。核心对齐引擎和 GUI 工作台经过测试，但仍可能存在边缘情况的 bug。欢迎提交 issue 或 pull request！_
