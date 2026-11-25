# 关键流程

[← 返回开发文档目录](README.md)

本页记录跨层调用链和不能随意破坏的顺序/绑定关系。单模块细节见[模块地图](modules.md)。

## 对话与动态分析

```text
POST /api/chat（SSE）
  → chat_service.run_turn
  → AgentRuntime.execute（tool-calling，失败回退 IntentParser）
  → WorkflowManager 分发
  → run_analysis：_skill_plan → ModalRouter.read_analysis_plan
  → R 脚本生成 → 确认卡（SSE confirm_card）
  → /api/confirm → confirm_flow（script_confirm / script_cancel / data_confirm）
  → Rscript 执行 → 结果、空间图、修复脚本登记回原 run
  → WorkflowRecorder / WRROC / SnapshotManager
  → SSE delta / tool_status / confirm_card / chart / done
  → web/src/lib/sse.ts → web/src/store/chat.ts
```

约束：

- `delta` 可带 `source=reasoning`，表示模型自述的思考增量；前端单独展示，
  不进入正文草稿、上下文、证据或导出。供应商常见字段为 `reasoning_content`
  或 `reasoning`，缺失时安静降级。
- 思考条与正式回答共用同一轮状态：正文开始后自动收起，结束后显示耗时；
  下一轮、切会话或失败且无正文时清空。多轮工具调用之间用空行分隔思考。
- 正文流式按轮判定：普通对话逐 chunk 下发；本轮真正调用了 `search_knowledge`
  之后才切换为整段缓冲，等工具结果回来再一次性下发。思考与工具状态不受影响。
- 无工具调用时走 `general_response`。
- 非单细胞分析与运行记忆是**非终态**：`run_analysis`（`analysis_type != single_cell`）
  和 `query_memory` 返回 `continue_tool_loop`，模型可继续检索或收口；分析产物
  （`results` 除 `knowledge_evidence` 外，以及顶层 `capsule_dir`/`run_id`）并入
  最终 `results`，与证据包一起落库。
- `search_knowledge` 默认启用，只读：检索证据（E 编号/来源）供模型继续推理，不生成答案、不写运行记录。
  `AGENT_KNOWLEDGE_TOOL` 设为 `legacy`（或 `off`/`false` 等停用值）会回退到旧 `query_knowledge`：
  由 LightRAG 直接作答并写运行记录，仅作兼容期排障，不再新增能力。
  `query_knowledge` 已弃用，只保留一个版本供回退；删除条件：一个发布版本内经日志/用户反馈确认无 legacy 使用，按 BREAKING 流程发布。
  用过检索的轮次必须引用真实 `[E#]`；服务不可用、没有证据、引用校验失败分别
  返回 `knowledge_unavailable` / `insufficient_evidence` / `citation_invalid`。
  证据包随助手消息存于 `results.knowledge_evidence` 供刷新后展开，保存完整正文；
  `answer` 回灌完整正文；`survey`/`exhaustive` 保留节选上限。回灌模型只保留 `E#`、
  标题、年份与来源关系；超过单次上限时按整条证据删减并显式报告省略数，正文本身不截断，
  也不返回截断的 JSON 前缀。证据包同时带 `scope`、`evidence_count`、`source_count`、
  `has_more`、`next_cursor` 与覆盖说明。下一轮历史准备只保留正文与显式论断，并清理旧消息
  里落库的证据清单与来源列表。
- 动态脚本由生成器产生，确认后才执行；已知失败保留尝试历史，用户可明确重试。
- 中断、成功、取消或无法核验的运行拒绝重跑；不把复制卡恢复 pending。
- 新确认请求独立恢复并核验原运行，不依赖 GET 恢复的请求上下文。
- 执行前持久化 running；成功事实先于后处理持久化。执行后的未知记录/消息异常保留卡片领取，
  不隐式重新执行。
- 无身份旧卡只在当前确认请求中沿兼容路径记录本次执行，不倒推原准备身份；刷新恢复仍使其过期。

## 受控单细胞五阶段

```text
POST /api/sessions/{sid}/local-input（无 LLM）
  → 校验 10x 目录/CSV → WorkflowManager → sc_qc.run_sc_qc
  → chat_service.register_qc_result → 证据与消息落库
QC：preview → approve（人工）→ filter（绑定精确细胞选择）
PCA：执行 → dimensions（人工维度记录）
聚类：neighbors / Louvain / UMAP → review（人工探索性接受）
marker：独立授权的全细胞 Presto marker → 结果待复核
```

约束：

- 每个阶段都需要独立授权；审批依据绑定输入、产物与身份。
- 阶段失败保留脚本、日志和元数据，不自动修复、重跑或命名细胞类型。
- 完成态产物由 `src/analysis/stage_verify.py` 校验；复核有效性由
  `src/analysis/review_standing.py` 与 `src/analysis/review_facts.py` 判定。
- QC 预览/完整性校验在 `src/analysis/qc_preview.py`；PCA 使用 dimension binding；
  聚类复核绑定已完成尝试；marker 复核通过 `src/analysis/sc_marker_reviews.py` 装配绑定视图。
- 已完成的父证据按授权时的快照校验，不因下游推进而失效。

## 文献证据问答（按需检索）

```text
POST /api/chat
  → 智能体按需调用 search_knowledge（tool_status search_knowledge）
  → 检索过的回合等工具结果回来再生成正文
  → 正文按 [E#] 引用校验 → 证据包落库 → done
```

`citation_invalid` 只提示展开证据面板，不把证据标题/片段/来源路径写进正文。

检索工具与普通对话共用同一证据包和同一引用校验。检索失败报
`knowledge_unavailable`；空结果报 `insufficient_evidence`；引用无效重试一次后报
`citation_invalid`。旧消息里的 `chat_mode=knowledge_qa` 只用于渲染兼容，不再选择
独立链路。

`search_knowledge` 按意图选择 `answer`（默认，8 条完整证据/硬上限 12）、`survey`
（约 20 个去重来源、每源最多 2 段节选）或 `exhaustive`（每页最多 50 个来源，
返回 `has_more`/`next_cursor`，覆盖说明不承诺找全）。`sources_only=true` 先取来源
清单，`source_id` 再取单个来源正文；游标绑定查询指纹，不能跨查询复用。

混合评测用例在 `evals/knowledge_tool_cases.jsonl`，入口
`python -m evals.run_knowledge_tool_eval`，三层分开：
`--scripted` 用脚本化 LLM + 假工作流只验证运行器契约；`--tools` 用真实模型 +
假工作流只报告工具选择与格式（`--live` 为弃用别名）；`--e2e` 才使用真实
WorkflowManager 与真实检索，只跑文献用例并报告回答与引用质量，基线比较仅在
同版本端到端报告之间生效。

## 记录、快照与溯源

- `WorkflowRecorder` 写 `.wrroc/<run_id>/workflow.json`。
- `SnapshotManager` 建 `snapshots/<run_id>/` Git worktree；同名快照直接复用，不 reset 覆盖。
- recorder 的 run 表为实例级 ContextVar，实例之间不共享。
- `python -m src.cli.replay <run_id>` 只恢复已有快照与依赖，不重跑分析。
- `python -m src.cli.records export session|project <id> --out record.zip` 导出记录；
  `--hash` 计算现存文件 SHA256，`--redact` 显式脱敏路径。

## 确认卡状态

动作/状态常量的唯一来源是 `src/ui/confirm_protocol.py`，前端对应
`web/src/lib/confirmState.ts`。跨层漂移由 `tests/unit/test_confirm_contract.py` 拦截。
确认请求必须重新核验会话、脚本、参数和绑定，不能只信前端回传。
