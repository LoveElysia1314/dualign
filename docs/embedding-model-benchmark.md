# 嵌入模型对比测评报告

> 主题：`leoipulsar/harrier-0.6b`（当前默认）vs `nemotron-embed:1b-q4`（候选）
> 日期：2026-08-19
> 语料：demo（《与天使相遇》第 1 话，中英对照，63 原文行 × 62 译文行）
> 结论先行：**Harrier 是当前系统更好的默认模型；Nemotron 目前不能作为 drop-in replacement**。
> 这并不等价于「Harrier 的 embedding 能力全面优于 Nemotron」——详细定性见第 9 章。

---

## 1. 背景与目标

Ollama 新增 `nemotron-embed:1b-q4` 后，需要判断它能否替代当前默认的 `leoipulsar/harrier-0.6b`。评测按以下约定：

- **正确对齐的文本对 → 得分越高越好**
- **错位对齐的文本对 → 得分越低越好**
- 综合考察 1:1 配对判别力与 2:1 / 1:2 整句·半句配对（merge/split）能力

## 2. 评测方法

### 2.1 Gold 基准

以当前模型（harrier-0.6b）对 demo 语料的完整对齐结果为 gold 基准（其结果被确认为正确）：

- 提取全部 1:1 正确对齐对（56 对）作为正例（positive）
- 对每个正例构造错位对：shift ±1/±2/±3 与随机错位，共 380 个负例
- 完整对齐采用与生产一致的 DP 引擎（同一套锚点/合并参数）

### 2.2 输入格式（关键方法论）

Embedding 模型的 prompt 是模型训练配方的一部分，**必须使用各模型的官方推荐 inference contract**，不能共享同一 instruction 前缀：

| 模型 | query 端（中文原文） | passage 端（英文译文） | pooling |
|---|---|---|---|
| harrier-0.6b | `Instruct: Retrieve the corresponding translation of the given text in another language\nQuery: {text}` | `{text}`（原样） | last-token |
| nemotron-embed:1b-q4 | `query: {text}` | `passage: {text}` | average |

> **教训**：首轮评测曾给两个模型共用同一 instruction 前缀（`Identify parallel sentences across languages\nQuery: `）。该前缀对 mean-pooling 的 Nemotron 注入公共方向（anisotropy），导致其所有文本对得分聚集在 0.8 附近、随机对均分高达 0.806——与 GGUF 发布者用正确格式测得"无关 pair 仅 0.044"严重不符。判定为**调用格式错误而非模型能力不足**，全部结论以修复后的官方格式为准。

### 2.3 评分方式

`SimilarityScorer`（余弦相似度，L2 归一化向量点积）。嵌入缓存键含「模型名 + instruction 哈希」，两模型天然隔离，互不污染。

## 3. 文本对得分评测（1:1 配对判别）

### 3.1 总体指标

| 指标 | harrier-0.6b | nemotron-embed:1b-q4 | 优劣方向 |
|---|---|---|---|
| 正确对均分 | **0.6189** | 0.5626 | 越高越好 |
| 错位对均分 | 0.4102 | **0.2890** | 越低越好 |
| 区分度 margin（正确−错位均分） | 0.2087 | **0.2736** | 越大越好 |
| 对级判别率（正确对 > 错位对） | **99.47%** | 98.95% | 越大越好 |
| Top-1 命中率（正确译文组内最高分） | 96.43% | 96.43% | 越大越好 |
| 与 gold 1:1 重合率（完整对齐） | 100% | 93.33% | — |

### 3.2 按错位类型细分

| 类型 | harrier 均分 | nemotron 均分 |
|---|---|---|
| 正确对齐 (gold) | 0.6189 | 0.5626 |
| shift±1 | 0.4195 | 0.3060 |
| shift±2 | 0.4133 | 0.2926 |
| shift±3 | 0.4142 | 0.2897 |
| random | 0.3785 | 0.2478 |

**Nemotron 错位越远分越低（梯度更陡），空间更干净；Harrier 负例梯度较平坦但整体分离良好。**

## 4. 空间健康度

> 目的：检测 embedding 空间是否存在 anisotropy / common-component 主导（前轮"全局 0.8 高分"即此病）。

| 指标 | harrier-0.6b | nemotron-embed:1b-q4 |
|---|---|---|
| 随机对均分（3850 对） | 0.3863 | **0.2536** |
| 随机对 P05 / P95 | 0.287 / 0.486 | **0.115 / 0.402** |
| 随机对标准差 | 0.060 | **0.087** |
| anisotropy 源池 | 0.5714 | **0.2769** |
| anisotropy 目标池 | 0.5420 | **0.4833** |
| anisotropy 混合池 | 0.4726 | **0.3178** |

**Nemotron 空间明显更健康**：随机对分布更分散、中心更低、anisotropy 全面更低——说明修复格式后其嵌入更少受公共分量主导。

## 5. 区分度指标

> 以 gold（56 例）为正类、错位对（380 例）为负类。

| 指标 | harrier | nemotron | 解读 |
|---|---|---|---|
| AUC | **0.9945** | 0.9839 | 均接近完美 |
| Cohen's d | **3.37** | 3.14 | 均 >3，分离极好 |
| 分布重叠系数 | **0.031** | 0.088 | A 更干净 |
| 正例 P5 / 负例 P95 | **0.551 / 0.510** | 0.439 / 0.432 | **关键差异** |
| 组内 rank=1 比例 | 98.21% | 98.21% | 持平 |
| 最优阈值 F1（P/R @阈值） | **0.948** (92%/98% @0.54) | 0.885 (88%/89% @0.47) | A 更优 |
| 组内 margin 均值 / 最小 | 0.131 / −0.010 | **0.180** / −0.060 | B 平均大、最差更差 |

### 5.1 核心洞察：两种判别模式（与阈值坐标系适配）

> **绝对分数不能跨模型比较**：余弦绝对刻度是模型自己的 calibration，`0.56 < 0.62` 没有模型质量意义（Nemotron 空间反而更干净，见第 4 章）。真正的问题不是「Nemotron 分数偏低」，而是**现有系统把 0.60 当作具有语义意义的固定阈值，而 Harrier 恰好在这个坐标系上训练/校准得非常合适**。这一项应命名为「**现有锚点评分机制适配性**」。

- **Harrier = 分数空间分离型**：正例 P5（0.551）> 负例 P95（0.510），存在绝对阈值同时覆盖 95% 正例与 95% 负例 → **天然适配固定门槛锚点策略**（当前 `ANCHOR_MIN_SCORE=0.60` 恰好匹配，正确对均分 0.619）。
- **Nemotron = 相对排序型**：正例 P5（0.439）几乎贴着负例 P95（0.432），绝对阈值无法干净分离 → 在 0.60 固定门槛下锚点率仅 36.8%（正确对过线率 39%）；但其**排序能力优秀**（组内 rank=1 同为 98.2%，margin 均值反而更高）。**若切换，必须为 Nemotron 单独 calibration（如调低门槛至 ~0.50）或改用相对判别策略。**

### 5.2 失败案例分析

两模型失败对高度重合，均为**语料自身的 hard cases**（相邻段落语义相近），非模型缺陷：

- 「周和她就读同一所高中…」 vs 「She consistently ranked first…」（同说她成绩/同级）
- 「可是，把露出那种表情的人放著不管…」 vs 同句式 shift 段落
- Nemotron 独有失败：短句「（干嘛待在那边淋雨？）」被多个相邻段落反超（gold 0.371，但负例最高 0.432）

两模型得分 Pearson 相关系数 0.84，判断趋向高度一致。

## 6. 整句/半句配对（merge/split）场景

> 对齐任务不仅 1:1，还有 2:1 合并、1:2 拆分。评测「整块得分 vs 完全拆开得分」的 advantage（正=识别应合并/拆分，负=判拆开更好即漏合并），gold 取自 Harrier 完整对齐的 merge/split 关系。

| case | 类型 | harrier advantage | nemotron advantage |
|---|---|---|---|
| (21,22)→(21,) | 2:1 | +0.0779 | **+0.0931** ✅ |
| **(38,39)→(38,)** | **2:1** | **+0.0094** | **−0.0314** ❌ |
| (6,)→(5,6) | 1:2 | +0.0181 | **+0.0481** ✅ |
| (32,)→(31,32) | 1:2 | +0.0070 | **−0.0041** ❌ |

**重点 case `(38,39)→(38,)`**（「藤宫同学，你找我有事？」+ 周心里感慨… → "Fujimiya-kun, do you need something?"）：用户记忆中的"合并前后分差细微"属实——Harrier advantage 仅 +0.0094。**Nemotron 在（6.2 节所述）未遵循其 inference contract 的实现下有 2 个 case 判反**（如该 case advantage −0.0314，判定"拆开更好"，会直接漏掉该合并）。按 6.2 的工程限制，**目前不能区分这是 Nemotron 的模型能力问题还是管线适配问题**——但对工程决策无影响：当前需要的是可直接替换的模型，而不是理论上调完系统后最好的模型。

### 6.1 伪合并 sanity（不乱合并测试）

对 1:1 正确对做伪合并（相邻两句源合并去匹配前句译文），正确模型应明显降分：

| 模型 | 平均惩罚 | 惩罚 > 0 比例 |
|---|---|---|
| harrier | 0.0556 | **96.4%** |
| nemotron | 0.0548 | **89.1%** |

Nemotron 有 10.9% 样本误判伪合并更好（最严重 −0.082）——**乱合并倾向更高**。

### 6.2 工程限制说明（结论降级依据）

对齐引擎的合并块编码路径（`encode_fn`）是单参数、**无侧感知**，合并块用原样文本编码——对需要 `passage:` 前缀的 Nemotron 不公平。这也解释了 Nemotron 在完整对齐中 merge/split 全部消失（5 个非 1:1 关系全变成 delete/insert）。

因此本节结论应降级为：

> **Nemotron 在当前 merge/split 实现下表现不稳；由于合并块没有遵循模型 inference contract，目前不能区分这是模型能力问题还是管线适配问题。**

**要公平评估 Nemotron 的真实管线 merge 能力，需先让引擎支持侧感知的合并块前缀编码。**

## 7. 完整对齐结果对比（官方格式）

| 指标 | harrier | nemotron |
|---|---|---|
| 关系数 (ops) | 61 | 65 |
| 1:1 对 | 56 | 60 |
| 合并 / 拆分 / 删除 / 插入 | 2/2/1/0 | 0/0/3/2 |
| 真锚点数 | 72 | 46 |
| 锚点率 | 57.60% | 36.80% |
| 对齐均分 | 0.6081 | 0.5204 |

Nemotron 锚点率大幅低于 Harrier，根因即 5.1 节：**现有锚点模块与 Nemotron 的校准坐标系不匹配**（0.60 门槛是 Harrier 坐标系上的合适阈值），而非判别力不足（其 AUC 0.984、Top-1 96.4%）。

## 8. 耗时观察（非正式）

> 未做最终正式测速（用户打游戏期间系统负载干扰，且已判定不切换，测速无必要）。记录已观测规律供参考：

- 编码为批量 `/api/embed`（非逐条 HTTP），生产走嵌入缓存
- 两模型**同时常驻时互相挤占显存**：被挤到 CPU/GPU 混合推理时吞吐可从 ~30 行/s 跌至 ~6 行/s
- 上一轮观测的 0.2s vs 18.4s 差异根因是**缓存命中 vs 冷启动**，非模型速度差
- 结论：显存充足的机器上建议保持单模型常驻；评测耗时需在系统空闲 + 独占 GPU 时进行

## 9. 结论与建议

### 9.1 总评

| 维度 | harrier-0.6b | nemotron-embed:1b-q4 | 胜者 |
|---|---|---|---|
| 1:1 配对判别（AUC/Top-1/判别率） | 0.995 / 96.4% / 99.5% | 0.984 / 96.4% / 99.0% | 平 |
| 空间健康度（random 对/anisotropy） | 0.386 / 0.473 | **0.254 / 0.318** | **B** |
| 区分度 margin | 0.209 | **0.274** | **B** |
| 锚点评分机制适配性（0.60 坐标系） | **✅ 天然匹配** | ❌ 需单独 calibration | **A** |
| merge/split 判定 | +0.028（全部正） | +0.026（2 个判反） | **A** |
| 乱合并倾向（伪合并惩罚） | **0.0556 / 96%** | 0.0548 / 89% | **A** |
| 完整对齐锚点率 | **57.6%** | 36.8%（门槛错配） | **A** |

### 9.2 当前模型结论定性

| 问题 | 当前答案 |
|---|---|
| 现在是否换 Nemotron？ | **不换** |
| Harrier 是否在当前系统里更好？ | **是，明显更适配**（判别力持平，但 0.60 坐标系、merge/split、合并块编码路径全部为它优化） |
| Nemotron embedding 是否「差」？ | **不是**，1:1 ranking 实际相当强（AUC 0.984、Top-1 96.4%、margin 更高、空间更健康） |
| Nemotron 是否值得继续投入工程适配？ | **暂时没必要**（当前要的是可直接替换的模型；调完系统后理论上更好的模型不是本次目标） |
| 下一步优化重点 | **Harrier instruction + 双向评分**（见 9.4 / 11 章） |
| 下一款模型评测时是否应沿用 0.60 threshold？ | **不应该**——每个模型应有自己的 calibration |
| 当前 benchmark 是否已有实际价值？ | **有**，但需扩大真实 Gold 集（见 11 章） |

### 9.3 建议

1. **维持 `leoipulsar/harrier-0.6b` 作为默认嵌入模型**——判别力与 Nemotron 持平，且天然适配现有 0.60 锚点门槛与合并块编码路径。
2. **Nemotron 保留为备选但暂不投入**：若未来采用相对判别策略或模型专属 calibration，其排序能力（margin 0.274 更高）与更健康的嵌入空间值得再评估；若再评，需先解决 6.2 节的侧感知合并块编码。
3. **最值得继续研究的是 Harrier 的 instruction**：首轮用项目默认 instruction（`Identify parallel sentences across languages`，双侧加前缀）测得正确对均分 0.771 / margin 0.283 / Top-1 98.25%，优于官方格式的 0.619 / 0.209 / 96.43%——**非官方 prompt 反而可能更适合小说平行文本任务**。下一步最有回报的实验是把 Harrier 的 prompt 当超参数系统测试（见 11.2）。

### 9.4 架构认识：评分校准应独立于嵌入模型

本次测试产生了一个有价值的架构认识：

> **Embedding 模型应该输出「相似度」，锚点模块应该负责模型专属的 score calibration。**

未来不应让 `ANCHOR_MIN_SCORE = 0.60` 直接绑定 raw cosine。更通用的做法：

```
raw cosine
    ↓  model-specific calibration（用 Gold 数据求 P/R 曲线）
alignment confidence [0,1]
    ↓  统一的 anchor threshold
```

这样 Harrier、Nemotron、未来其他模型可以复用同一套 DP 超参数，而不是每换一个 embedding 就重新解释「0.60 是什么意思」。**但当前阶段不为支持 Nemotron 立即重构这一层**——先把 Harrier 的 instruction 与双向评分测透，投资回报更高。

## 10. 复现与产物

> **归档说明（2026-08-19）**：评测脚本与全部中间产物已从工作区打包至
> `D:\drzqr\Documents\dualign-embedding-benchmark\`（保持原 `demo/` 相对结构，
> 激活项目 venv 后 `cd` 到该目录即可 `python -m demo.xxx` 原样复跑，输出写入
> 归档目录自身，不影响工作区）。完整归档说明见该目录 `README.md`。

评测脚本（均可复用）：

| 脚本 | 用途 | 备注 |
|---|---|---|
| `model_compare.py` | 文本对得分对比 + 空间健康度 | `python -m demo.model_compare [模型B名]` |
| `analyze_scores.py` | 离线分数深析（区分度/失败/阈值） | 纯读 CSV，秒出 |
| `merge_analysis.py` | merge/split 整句半句场景 | 批量两阶段编码 |
| `model_benchmark_time.py` | 编码耗时基准（无缓存+独占 GPU） | 本次未正式测速 |
| `threshold_sweep.py` | 锚点阈值 0.25–0.80 离线扫描 | 纯读 CSV，秒出 |
| `instruction_compare.py` | Harrier instruction 超参测试（A/B/C/D） | 需 Ollama 常驻 |

产物（归档目录 `demo/output/`）：

| 文件 | 内容 |
|---|---|
| `model_compare_scores.csv` | 436 对逐对得分 |
| `model_compare_health.csv` | 随机对分布 + anisotropy |
| `model_compare_report.md` | 文本对得分报告 |
| `model_compare_scores_analysis.md` | 区分度/失败/阈值深析 |
| `merge_analysis_report.md` / `merge_analysis.csv` | merge/split 场景 |
| `threshold_sweep_report.md` / `threshold_sweep.csv` | 阈值扫描全表 |
| `instruction_compare_report.md` / `instruction_compare.csv` | Harrier 模板全指标 |

## 11. 统计局限与后续工作

### 11.1 样本局限

当前 Gold 集：**56 个真实 positive + 380 个构造 negative + 一个章节**。

- 足够发现**巨大差异**（如格式修复前的 anisotropy 病、0.60 坐标系错配），也足够决定「现在要不要换模型」；
- 但**不足以可靠地区分细微指标差异**（AUC 0.9945 vs 0.9839、Top-1 96.43% vs 96.43%、判别率 99.47% vs 98.95%）——56 个 query 下单个样本约 1.8 个百分点（Top-1 错 2 个 = 96.43%，错 3 个 = 94.64%）。

因此最有价值的扩展不是增加 random negatives，而是**扩大真实正例**：最终 benchmark 建议做到 **500–1000 个人工确认的 alignment units**，并故意覆盖：

- 极短对白 / 长句 / 心理描写 / 对话连续段
- 一中一英 / 一中多英 / 多中一英
- 人名很多的句子 / 内容高度相似的相邻句 / 较强意译的句子

### 11.2 后续实验建议（按投资回报排序）

**① Harrier instruction 超参数系统测试**（最高优先）：把 prompt 当超参数，至少比较：

| 变体 | query 模板 |
|---|---|
| A. 当前生产版 | `Identify parallel sentences across languages\nQuery: {text}` |
| B. 明确翻译关系 | `Retrieve the corresponding translation across languages\nQuery: {text}` |
| C. 更直接 | `Find the parallel sentence in another language\nQuery: {text}` |
| D. 官方 bitext 模板 | Harrier 官方 `bitext_query` 对应模板 |

跑全指标：Top-1、AUC、Cohen's d、positive P5 / negative P95、hardest-negative margin、random cosine、anisotropy、merge/split、最终 DP alignment accuracy——很可能还能从现有 Harrier 中榨出性能。

**② 正反方向对称性（双向评分）**：当前仅测中文 query → 英文 passage；小说平行匹配是天然对称问题，应补测英文 query → 中文 passage，并定义双向得分

$$S(x,y)=\frac{S_{\mathrm{zh\to en}}(x,y)+S_{\mathrm{en\to zh}}(y,x)}{2}$$

（或几何平均）。若 Harrier / Nemotron 存在明显 query-passage 非对称性，双向得分可能特别适合本场景，并可能改善最头疼的 merge/split hard case——真正的平行关系应在两个方向上都成立。

**③ Q4 量化因素隔离**：当前对比实为 `harrier-0.6b` checkpoint vs **Nemotron-1B-Q4_K_M**。若想回答「Nemotron 为什么没赢」，可补测 Q8_0 / F16（能运行的话），只跑 436 对 scorer 即可。若 Q8/F16 的 hard-negative failures、P5 positive、merge/split 显著改善，则 Q4 量化是重要原因——embedding 做 nearest-neighbour ranking 时应比生成任务更警惕 Q4。
