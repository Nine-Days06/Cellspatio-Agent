# 排障

[← 返回开发文档目录](README.md)

优先看后端日志与 `/api/*` 的 `{"detail", "code"}` 响应；错误码映射在代码中，
不要按错误文案反推状态。

| 现象 | 先检查 |
|---|---|
| 页面 404 或白屏 | 是否已 `npm run build` 生成 `web/dist/` |
| 前端开发模式 API 404 | Vite 没有 `/api` 代理，改用后端挂载构建产物联调 |
| PG 用例被跳过 | 是否运行 `.\start_pg.ps1`；测试库是否为 `rag_test` |
| PG 异步操作冲突 | LightRAG 是否共用 `async_runtime` 的进程级事件循环 |
| 知识库检索为空 | Ollama 是否运行、`bge-m3` 是否存在、workspace 是否一致 |
| 受控阶段被拒绝执行 | 固定 R/Seurat 版本、上游授权快照、产物身份与 revision 是否匹配 |
| 端口被占用 | 后端 8600、公共证据 8700、PG 5433 是否已有进程 |
| 前端构建通过但页面异常 | 用后端挂载 `web/dist/` 验证，而不是只看 `npm run dev` |

## 日志与现场

- 内嵌 PG 日志：`runtime/pgdata/pg.log`。
- 后端日志：`python -m src.api` 的 stdout/stderr；未结构化异常统一返回 500
  `{"detail": "服务内部错误"}`。
- 运行记录：`.wrroc/<run_id>/workflow.json`；快照：`snapshots/<run_id>/`。
- 受控阶段失败保留脚本、日志和 metadata，先看对应 stage 目录再决定是否重试。

## 本地排障文档

更细的本机实测与历史故障记录在 `docs/agents/troubleshooting.md`（本地，不提交）。
新的可复现排障结论先写该文件，再把跨任务约束提炼到 `AGENTS.md` 或本页。

## 不要做的事

- 不要用删除数据库、清空 `data/` 或重建快照来“快速修复”未知问题。
- 不要在失败后自动重跑 R 或受控阶段；已知失败需要用户明确重试。
- 不要为提高检索命中率随意更换 workspace；会导致历史数据读不到。
