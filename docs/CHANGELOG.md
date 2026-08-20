# 更新日志

> 版本格式：Semantic Versioning（主版本.次版本.修订）

---

## Unreleased

### 🔧 变更

- 将 `report.json` 确立为唯一对齐与校订工作状态，记录中性文档 A/B、块级关系、正文哈希与非敏感生成来源。
- GUI 普通保存只更新报告；“固化修改”可分别选择文档 A/B 的合并、拆分和校订，并以可恢复的三文件事务写入正文和重建后的报告。
- 新增 `dualign solidify` 预览/应用命令、JSON/TOML 配置、五种预设以及部分固化后的剩余操作重锚。
- 固化策略加入双侧“删除文本对”；占位保持为报告操作。双侧 N:M 合并/拆分改为原子固化，审核标记在当前报告或固化历史中保留。
- `align_documents(reset_work_state=True)` 可复用有效对齐关系并重建干净工作报告，避免重新对齐时误带旧 `flag`、审核和评分状态。
- CLI `align` 与 Python `align_documents()` 只生成报告，不再隐式导出等行 Markdown。
- 正文校订不再要求两侧行数相等，GUI 与公共参数逐步统一为文档 A/B 术语。
- 删除旧报告迁移、`repaired/`、`.bak`、晋升和 YAML 双状态支持。
- 将按章节分散的 `vecs.db` 合并为全局行级嵌入缓存，真正支持跨文档复用。
- 缓存写入改为不可变键语义，增加 SQLite 忙等待和大批量查询分片。
- 新增 `scripts/migrate_embedding_cache.py`，支持幂等迁移、逐库校验及安全移除旧分库。
- Ollama 嵌入批次默认限制为 128 条；tokenizer 拒绝批次时自动二分重试，并保留服务端错误详情。

---

## 0.8.0 (2026-08-18)

### ✨ 新功能

- **AI 审校提供方增强**：支持 OpenAI Responses API 工具格式与 Ollama 兼容后端
- **推理强度配置**：DeepSeek 默认使用 `low`，并允许透传 `reasoning_effort`
- **uv 开发环境**：新增 Python 版本声明、依赖锁文件和 Windows 一键启动脚本

### 🔧 变更

- **AI Agent 可靠性**：统一工具目标参数，完善拒绝后的循环恢复和工具调用校验
- **嵌入服务韧性**：串行化 Ollama 请求，按层级缩减批次，并扩展 tokenizer 错误重试
- **全栈稳定性整理**：收紧 DP 边界，清理缓存连接，并统一模型、服务层与 GUI 的生命周期和异常报告
- **版本元数据规范化**：以 `pyproject.toml` 为唯一版本源，运行时从安装包元数据读取版本
- **统一代码格式**：将 Black 纳入开发依赖并格式化全仓库 Python 代码

### 🐛 修复

- 修复锚点统计解包错误和多处 `EmbeddingCache` 连接泄漏
- 修复 API Key 持久化、调试导出路径与 GUI 定时器泄漏
- 修复 Responses API 扁平工具格式缺少 `name` 时的兼容问题
- 修复发布脚本误用系统 Python、旧代码页输出失败和无效隐藏导入
- 增加 AI Agent 工具契约测试并强化 CLI 测试隔离

---

## 0.7.0 (2026-06-xx)

### ✨ 新功能

- **跨 snap 校订**：AI Agent 支持 `edit` 连续 snap 范围（如 `snap_range="10-13"`）
- **嵌入指令（Instruction）机制**：编码时自动添加双语平行对齐任务描述，显著提升语义区分度
- **质量门控 G1/G2/G3**：真锚点密度、孤行占比、合并触顶三级质量评估
- **ProviderManager**：模型提供方管理，支持 Ollama / LM Studio / 自定义 API 切换

### 🔧 变更

- **重构对齐引擎**：Phase 1→5 流水线，从递归锚点 + 赝锚点 + 全局枚举合并 + 单次 DP 最终决选
    - 真锚点搜索改为递归迭代（分段后对手减少 → 被遮挡锚点浮现）
    - 移除 restricted/full DP 双轨，合并为单一 DP
    - 移除 pure/mixed/adjacent 间隙类型划分
- **AI Agent 重构**：移除 auto*note/would*\* 暴露给 AI，改为两层文本模型
- **嵌入缓存从 NPZ 迁移到 SQLite**：支持行级缓存，跨文档共享
- **Ollama AI 审校后端移除**：仅嵌入服务使用 Ollama，AI 审校统一使用 DeepSeek API
- **CollapsibleSection 回退为 QGroupBox**：消除 Windows DWM 启动闪烁
- **SnapState 三层模型**：原始事实 / 当前状态 / 处理历史

### 🐛 修复

- GUI 启动闪烁（root cause: CollapsibleSection HWND）
- 多个索引漂移问题（不可变快照 + append-only log 根治）
- ScoreManager worker invokeMethod 崩溃

### 💥 破坏性变更

- `RepairAction.data` 中 `source` 字段移入顶层（`action.source`）
- `ChapterContext` 移除 `op_statuses`、`src_out`、`tgt_out`
- 移除 `OllamaSimulatedBackend`（AI 审校不再支持 Ollama 后端）
- `report.json` 中 `ops` 字段使用 `{"s": [...], "t": [...], "sc": ...}` 格式
- 缓存目录结构调整（迁移到 SQLite `vecs.db`）

---

## (2026-05-xx)

- 首次公开发布
- 对齐引擎 Phase 1→4（不含 Phase 5 批量编码）
- GUI 工作台（PySide6）
- AI 审校代理 v1（含 auto*note/would*\* 机制）
- CLI 对齐流水线
- NPZ 格式嵌入缓存
