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
| `src/knowledge/qa.py` | 五阶段问答、证据与 package |
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

用例在 `evals/qa_cases.jsonl`；`evals/run_qa_eval.py` 跑五阶段问答，
`evals/qa_review.py` 记录人工复核。质量门槛只认按 `answer_id` 记录的复核事实。
