# 提示词评测（pubmed-etl）

用固定金标量化 `llm_validator` 筛选提示词的质量，避免「改提示词靠感觉」。

## 目录

```
eval/
├── annotation_guide.md      # 金标标注指南（判定口径的唯一真源）
├── prompts/
│   ├── baseline_v1.txt      # 第一版生产提示词（改前快照）
│   ├── candidate_v2.txt     # 当前生产版本（已落地到 cleaner/llm_validator.py）
│   ├── candidate_v3.txt     # 试验：收紧综述/无摘要观点文（未采用）
│   └── candidate_v4.txt     # 试验：仅收紧无摘要观点文（未采用）
├── run_prompt_eval.py       # 跑评测（模型 = .env 的 OPENAI_MODEL）
├── score_prompt_eval.py     # 单版本计分 + Markdown 报告
├── compare_runs.py          # 多版本横向对比
└── sample_from_db.py        # 从本地库分层抽样（产出 data/eval_v2/samples.jsonl）
```

数据在 `data/eval_v2/`（运行时数据，不入 git）：`samples.jsonl`（250 条题录）、
`gold.jsonl`（金标，唯一真源）、`run_*.jsonl`（各版本判定结果）。

## 常用命令

```bash
cd pubmed-etl
python -m eval.sample_from_db                                    # 重新抽样
python -m eval.run_prompt_eval --prompt eval/prompts/candidate_v2.txt \
       --out data/eval_v2/run_x.jsonl --concurrency 6            # 跑一个版本
python -m eval.score_prompt_eval --run data/eval_v2/run_x.jsonl  # 计分
python -m eval.compare_runs --runs data/eval_v2/run_a.jsonl data/eval_v2/run_b.jsonl
```

`score_prompt_eval` 和 `compare_runs` 都会用 `--gold`（默认 `data/eval_v2/gold.jsonl`）
**覆盖 run 文件里的 gold 快照**——金标修订过多轮，run 里的旧值不可信。

## 抽样分层（250 条）

| 层 | n | 抽样池 | 用途 |
|---|---|---|---|
| L2a_proteomics | 60 | 7,897 | CyTOF / IMC / MIBI / CODEX / 单细胞蛋白组（含大量同形异义噪声，如 99mTc-MIBI 核医学、Codex 食品法典） |
| L2b_spatial_variants | 40 | 3,600 | 空间多组学变体（spatial omics / spatially resolved 等） |
| L2c_misc_variants | 20 | 5,983 | RNA velocity / cell atlas / single-cell sequencing |
| L3_review | 50 | 11,334 | 综述层（判断「主题相关」边界） |
| L4_opinion | 40 | 1,151 | Letter / Comment / Editorial（含无摘要） |
| L5_noise | 40 | 1,440 | 「spatially resolved」但不含任何组学词的噪声 |

## 结果（2026-10-18，金标 250 条 REL 81 / NOT 169）

| 版本 | acc | prec | rec | F1 | kappa | FP | FN |
|---|---|---|---|---|---|---|---|
| baseline_v1 | 0.800 | 0.650 | 0.827 | 0.728 | 0.574 | 36 | 14 |
| **candidate_v2（生产）** | **0.984** | **0.953** | **1.000** | **0.976** | **0.964** | **4** | **0** |
| candidate_v3 | 0.968 | 0.974 | 0.926 | 0.949 | 0.926 | 2 | 6 |
| candidate_v4 | 0.960 | 0.928 | 0.951 | 0.939 | 0.909 | 6 | 4 |

### 金标整改（2026-10-18，250 → 228 条）

无摘要文献**不进测试集、不进 LLM 判定**，单独抽出人工审核（见下节）。据此：

- 移除 22 条无摘要样本 → 测试集 **228 条**，REL 73 / NOT 155；
- `L2c-002` 改判 **RELEVANT**（用户裁决：综述系统评述单细胞测序在卵巢储备研究中的应用，
  属「组学的应用」，应收）；
- `L4_-033` 改判 **RELEVANT**（用户裁决）：摘要通篇讲五项 snRNA-seq/snATAC-seq 研究，
  与同层已判 REL 的 `L4_-014`/`L4_-010`/`L4_-026` 同模式。原判 NOT 是金标自相矛盾。
  终态 **228 条，REL 74 / NOT 154**。

### 结果（2026-10-18 终态金标，228 条 REL 74 / NOT 154）

每个版本跑多次取均值——单次结果受噪声影响达 4 个百分点（见下节）：

| 版本 | 次数 | acc | 范围 | prec | rec | F1 | kappa | FP | FN |
|---|---|---|---|---|---|---|---|---|---|
| baseline_v1 | 2 | 0.792 | 0.772–0.811 | 0.644 | 0.797 | 0.712 | 0.552 | 32.5 | 15.0 |
| **candidate_v2（生产）** | **3** | **0.978** | **0.965–0.996** | **0.977** | **0.955** | **0.966** | **0.950** | **1.7** | **3.3** |
| candidate_v3 | 1 | 0.974 | — | 0.986 | 0.932 | 0.958 | 0.939 | 1.0 | 5.0 |
| candidate_v4 | 1 | 0.974 | — | 0.959 | 0.959 | 0.959 | 0.940 | 3.0 | 3.0 |
| candidate_v5（已废弃） | 3 | 0.952 | 0.939–0.969 | 0.957 | 0.892 | 0.923 | 0.888 | 3.0 | 8.0 |

**结论：candidate_v2 最好，v3/v4 与之实质等价。** 选 v2 的理由是召回——
v2 FN 均值 3.3，v3 是 5.0，精确率高 0.009（噪声级）却多漏 1.7 篇，净亏。

`candidate_v5` 已废弃，见下节。

### candidate_v5 为何失败（教训）

v5 试图修两类边界：①「公共数据实质性再分析」与「基因→细胞类型定位」的界线；
②方法学文献（新平台/新算法）即使验证数据非人类也收。**结果全面变差：FN 3.3 → 8.0。**

- **改动①过度泛化**：新反例写得太硬，把真正的组学再分析也打掉。`L2c-011`（bulk 多组学
  + 单细胞构建 TP53 预后模型，含无偏聚类）、`L2c-013`（微阵列联合单细胞分析共病机制）、
  `L3_-045`（作者自行再分析两个人类前列腺癌 scRNA-seq 数据集）三条**三次全错**，模型理由
  一律是「把少数基因定位到已知细胞类型，未产出新亚群/通讯/轨迹」。
- **改动②被用作豁免**：`L2a-051`（金标 NOT：数据全来自小鼠）模型答「虽然数据来自小鼠，
  但核心方法就是单细胞分辨率组学测量」；`L2a-054`（金标 NOT：测的是细胞外囊泡 EV）模型
  把「细胞级」偷换成「单颗粒级」。

**教训**：给模型列「算/不算」清单，它会机械套用，而真实文献恰好落在清单缝隙里。
v2 有效是因为给的是目标导向描述（「你自己产出组学数据吗」），不是清单匹配。
为了修 4 条边界误收，换来 10 条新漏收，明确是亏的。

### 无摘要文献处理

无摘要时 LLM 只能靠标题猜，实测是误判集中地（把 5-ALA 荧光成像的 letter 当成单细胞组学收录）。
处理方式：**不进 LLM 判定，单独出表人工审核**。

```bash
python main.py --step export-no-abstract   # 导出 data/output/no_abstract_manual_<ts>.csv
# 人工填 human_review 列（Y/N）
python main.py --step import-review --csv <上面的 csv>
```

生产库当前有 **1,228 篇**无摘要文献待人工审核。回写用 upsert（`llm_verdict` 留 NULL、
只写 `human_review`），所以既不会被当成「LLM 已判 RELEVANT」自动导出，人工判 Y 后又能正常导出。

### 评测噪声（重要）

**同一份提示词重复跑，结果不同。** 实测（temperature=0，`[opencode]deepseek-v4-flash`）：

- `candidate_v2` 三次：acc 0.996 / 0.974 / 0.965，极差 **3.1 个百分点**；
- `baseline_v1` 两次：acc 0.811 / 0.772，极差 **3.9 个百分点**；
- `candidate_v5` 三次：acc 0.969 / 0.947 / 0.939，极差 **3.0 个百分点**。

所以：**版本间差异 < 4 个百分点不可信**。baseline→v2 的 19 个百分点差距远超噪声，
但 v2/v3/v4 之间的 0.4–0.8 个百分点差异在噪声内——不要据此认定谁更好，
只能说三者等价。要分辨小差异，必须每版跑 ≥3 次取均值。

**v2 无系统性缺陷**：三次并集 12 条误判中，10 条只错 1 次、2 条错 2 次，
**没有任何一条三次全错**。剩余误差全是边界样本的随机抖动，已到天花板。

## 提示词演进要点

`candidate_v2` 相对 `baseline_v1` 的 4 处实质改动（每条都由具体误判驱动）：

1. **判断顺序第 3 步**：把「是组学平台（Visium/MERFISH/…清单）吗」改为「空间组学测量三类都算
   （空间转录组 + 空间蛋白组 + 空间代谢组/质谱成像）」。**根因**：baseline 的平台清单不含 MSI，
   导致 MALDI-MSI / DESI / SIMS 论文被系统性排除（5 条 FN）。
2. **收录 7**：明确「CyTOF/IMC/MIBI/CODEX 一律收录，无论焦点是信号通路磷酸化还是免疫表型」。
   **根因**：与排除 1 的「磷酸化流式」黑名单冲突，模型据此排除 CyTOF 信号通路研究（4 条 FN）。
3. **收录 11**：综述从「主题相关即收录」收紧为「核心议题必须是组学本身，仅顺带提一句不算」。
   **根因**：过宽导致 L3 层 15 条 FP。
4. **新增收录 12**：无摘要的 letter/comment/editorial，标题明确指向组学即收录。

## 已知局限

- **金标 228 条**，单条误判影响 accuracy 0.4 个百分点；分层指标（每层 20–60 条）更不稳。
- **金标有边界争议**：`L2c-002`（组学的应用算相关）与 `L4_-033`（评论主旨即组学技术）
  已按用户裁决改判 RELEVANT；但「用单细胞做的生物学综述/评论」这条线仍模糊——
  判据是「标题中心词是组学还是生物学对象」，实际操作时两者常混杂。
- **评测集与生产集口径已对齐**：无摘要样本从测试集移除，生产管线同样跳过 LLM 判定。
- **剩余误判已无法靠改提示词消除**：v2 三次无一条稳定错，说明剩下的是边界样本抖动。
  再改提示词只会把误差从一个方向推到另一个方向（v5 就是实例）。
