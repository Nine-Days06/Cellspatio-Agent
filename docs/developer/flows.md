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
- 无工具调用时走 `general_response`。
- 开启 `AGENT_KNOWLEDGE_TOOL=search` 后多一个只读工具 `search_knowledge`：
  只检索证据（E 编号/来源）供模型继续推理，不生成答案、不写运行记录。
  用过检索的轮次必须引用真实 `[E#]`；服务不可用、没有证据、引用校验失败分别
  返回 `knowledge_unavailable` / `insufficient_evidence` / `citation_invalid`。
  证据包随助手消息存于 `results.knowledge_evidence` 供刷新后展开；模型上下文只
  取正文与显式论断，不重放证据原文/来源。
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

## 知识问答

```text
POST /api/chat mode=knowledge_qa
  → tool_status knowledge_qa_retrieval start → 检索 → 证据 → end
  → tool_status knowledge_qa_answer start → 流式生成（思考走 delta source=reasoning）
  → 正式 JSON 完整缓冲 → 证据校验 → end
  → 回答落库 → done
```

两个阶段事件复用 `tool_status`，不新增 SSE 类型；前端只显示最新进入且未结束的阶段。
阶段事件只做过程展示，不参与 busy 收尾。

生成阶段优先使用供应商流式接口；思考增量实时外发，正式内容在缓冲区内拼成完整
JSON，校验通过后才落库和随 `done` 展示。流式在产生任何内容或思考前失败时回退
一次非流式调用；已经产生内容后中断则按生成失败收口，不重复请求。

评测只做技术跑批，不自动判定回答正确性。用例在 `evals/qa_cases.jsonl`，入口在
`evals/run_qa_eval.py`。

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
