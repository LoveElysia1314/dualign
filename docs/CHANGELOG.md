# 更新日志

> 版本格式：Semantic Versioning（主版本.次版本.修订）

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
- **AI Agent 重构为 v2**：移除 auto*note/would*\* 暴露给 AI，改为两层文本模型
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
