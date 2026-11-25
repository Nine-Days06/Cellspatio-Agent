# 知识库

[← 返回开发文档目录](README.md)

知识库复用 LightRAG，主库、方法库和 KG 记忆共用 PostgreSQL/pgvector 存储与同一个异步运行时。

## 组件

| 路径 | 职责 |
|---|---|
| `src/knowledge/lightrag_client.py` | LightRAG 封装、workspace、PG 四类存储 |
| `src/knowledge/async_runtime.py` | 进程级共享事件循环；所有 PG 后端 LightRAG 共用 |
| `src/knowledge/knowledge_index.py` | 覆盖索引；明确区分 `None`（无法确认）与空集 |
| `src/knowledge/knowledge_admin.py` | 运维状态、重排、pipeline 驱动 |
| `src/knowledge/qa.py` | 只读检索服务：chunk → 证据/来源、失败分类；不生成答案 |
| `src/knowledge/ollama_runtime.py` | Ollama 启停与空闲生命周期 |
| `src/knowledge/prompt_profile.py` | 领域实体类型与抽取规则注入 |

## 运行时边界

PG 后端 LightRAG 的连接池、事件循环和 workspace 是进程级共享资源。修改
`src/knowledge/async_runtime.py` 或 `src/knowledge/lightrag_client.py` 前，先核对
共享单例、稳定 workspace 和退出顺序；本机排障细节与历史实测保留在
`docs/agents/troubleshooting.md`（本地，不提交），本页只保留跨模块结论。

## Ollama 生命周期

模型存在性以服务端 `/api/tags` 为准；启停、自动下载、空闲关闭和预热由 `OLLAMA_*`
配置控制，具体键值和 `status()` / `start_mode()` 契约见
`src/knowledge/ollama_runtime.py` 与配置说明。

## 导入与问答

- 文献 ETL 导出后由 `src/knowledge/import_cli.py` 导入；批次名/列契约在
  `src/article_batches.py`。
- 导入前可运行 `build_pubtator_cache.py` 预计算基因 ID，避免重复 HTTP。
- `apply_domain_rules` 幂等注入实体类型与领域规则。
- 知识覆盖判断在 `src/control/kb_coverage.py`；自动补库默认关闭，仅
  `KB_AUTO_INGEST=1` 开启。
- 问答需要 Ollama embedding 与一个 LLM 供应商；只做受控单细胞 QC → marker 可以离线。

## 评测

文献证据链路的混合评测在 `evals/knowledge_tool_cases.jsonl`，入口为
`python -m evals.run_knowledge_tool_eval`，分三层：
`--scripted` 用脚本化 LLM + 假工作流只校验运行器与阈值；`--tools` 用真实模型 +
假工作流只报告工具选择与格式（`--live` 为弃用别名）；`--e2e` 使用真实模型、
真实 WorkflowManager 与真实检索，只跑 literature/strict_literature 用例（分析与
记忆类留在工具选择层），才支撑“不差于旧链路”的质量结论。端到端层若误用假工作流
会直接失败，不会静默降级。
`evals/run_eval.py` 按进程环境运行，默认校验 `search_knowledge` 工具路由；
它不会自动切换到 legacy，旧 `query_knowledge` 路由已不再是默认链路基线。
旧五阶段问答评测（`evals/run_qa_eval.py`）已随独立问答入口删除；回答质量统一由
上面的混合评测覆盖，评测不记录人工复核，也不自动判定回答正确性。
