# 金标标注指南（新口径，2026-10-18）

> 本指南用于构建提示词评测金标集。标注对象：PubMed 题录（标题 + 摘要）。
> 目标：判断文献是否应被收录进「人类单细胞与时空组学」知识库（方法库 + 知识问答库）。

## 判定任务

对每篇文献输出二值判定：

- `RELEVANT` —— 应收录
- `NOT_RELEVANT` —— 不应收录

## 核心判据

**这篇文献是否与人类单细胞分辨率或空间位置分辨率的组学研究相关。**

1. **组学测量**是必要条件（转录组/蛋白组/基因组/表观组/代谢组等大规模分子测量）。
   仅出现 "single cell"（如膜片钳电生理、显微成像、流式细胞术计数、有限稀释克隆、
   AFM 细胞力学、拉曼光谱脂质分布、钙成像）而**无组学测量**的文献一律 `NOT_RELEVANT`。
2. **单细胞或空间分辨率**：scRNA-seq/scATAC-seq/CITE-seq/CyTOF/IMC/MIBI/CODEX/
   Visium/MERFISH/spatial transcriptomics 等单细胞或空间平台；或对这些数据的方法学研究
   （算法、基准测试、数据库、工作流）。
3. **人类相关性**：研究人类样本/细胞系/患者，或以人类数据为主要分析对象；
   非人类物种的单细胞研究若引用人类数据集做参考图谱/比较基准，也算相关。

## 新口径规则（与旧口径的差异，务必遵守）

| 类型 | 判定 |
|---|---|
| **综述**（review） | **严格口径**：综述的**核心议题必须是单细胞/空间组学本身** → `RELEVANT`，不要求自己产出数据。可操作判据看**标题中心词**：中心词是组学技术/平台/数据资源/图谱（如 "Single-cell metabolomics"、"Spatial transcriptomics in cancer"、"The Human Cell Atlas"）→ 收；中心词是生物学对象（某器官功能、某疾病、某细胞类型、某调控机制），单细胞/空间只是方式状语或修饰语（如 "A New Understanding, Guided by Single-Cell Sequencing, of the Ovarian Reserve"）→ `NOT_RELEVANT` |
| **letter / comment / editorial / news** | 不一票否决，按内容判断：若含实质方法学贡献或领域述评（如对某方法的技术评论、数据资源通告）→ `RELEVANT`；纯致谢/勘误/无实质内容 → `NOT_RELEVANT`。**评论/述评类文章的核心议题是生物学机制（即使评述对象是单细胞研究）→ `NOT_RELEVANT`**，与综述同口径 |
| **单细胞/空间蛋白组**（CyTOF、imaging mass cytometry、mass cytometry、MIBI、CODEX、single-cell proteomics、spatial proteomics） | 属组学测量，按判据正常收录 |
| **空间多组学变体**（spatial omics、spatially resolved、spatial genomics/epigenomics/metabolomics） | 按判据判断：spatially resolved + 组学测量 → `RELEVANT`；"spatially resolved" 出现在非组学语境（如成像、解剖、物理化学）→ `NOT_RELEVANT` |
| **质谱成像**（MALDI-MSI、DESI、nano-DESI、SIMS、空气电离源成像质谱） | 属空间代谢组/脂质组测量 → `RELEVANT`（不要因「不在 Visium/MERFISH 清单里」而排除） |
| **撤稿/更正/勘误/关注声明** | 一律 `NOT_RELEVANT`（内容不可信） |
| **会议摘要** | `NOT_RELEVANT`（无实质内容） |

> **综述口径变更记录（2026-10-18）**：本条原为「主题落在范围内即收录」，实测导致模型把
> 「摘要里出现一句 scRNA-seq」的生物学综述全部误收（L3 层 FP 15/50）。经用户裁决改为
> 严格口径：核心议题必须是组学本身。据此重标 15 条金标（REL 102→80）。


## 易错情形提醒

- "single cell" 电生理/成像/力学/克隆 → `NOT_RELEVANT`（无组学）
- bulk 转录组 + 计算去卷积（deconvolution），有单细胞参考图谱参与 → `RELEVANT`；
  纯 bulk 无单细胞参与 → `NOT_RELEVANT`
- 空转录组学方法论文（仅模拟数据/ Benchmark）→ `RELEVANT`（方法库需要）
- 单细胞方向数据库/资源论文（cell atlas、测序数据资源）→ `RELEVANT`
- 标题含关键词但摘要明确是其他领域（如材料科学 "spatially resolved Raman"）→ `NOT_RELEVANT`
- 研究对象为动物/植物且与人类无关 → `NOT_RELEVANT`

## 输出格式

每篇一行 JSON：`{"sample_id": "...", "gold": "RELEVANT|NOT_RELEVANT", "reason": "一句话依据"}`
