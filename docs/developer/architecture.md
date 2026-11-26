# 架构

[← 返回开发文档目录](README.md)

主项目直接位于仓库根目录：`src/` 是 Python 后端，`web/` 是 React 前端，
`pubmed-etl/` 是独立文献 ETL 子项目。

## 分层

```text
浏览器 / CLI
  → src/api（FastAPI，:8600）或 src/main（CellSpatioAgent）
  → src/control（对话、工具路由、技能、工作流、记录）
  → src/analysis（受控单细胞固定工具、动态 R 脚本、R 执行）
  → src/ui / src/knowledge / src/evidence（状态、知识库、证据）
  → SQLite 会话库、本地 data/、PostgreSQL + pgvector、LightRAG
```

| 层 | 技术 | 主要入口 |
|---|---|---|
| Web 服务 | FastAPI + uvicorn，单进程 | `src/api/__main__.py`、`src/api/app.py` |
| 前端 | React 18 + Vite + TypeScript + Tailwind 4 + zustand | `web/src/pages/ChatPage.tsx`、`web/src/lib/sse.ts` |
| 控制层 | Python，tool-calling + 意图回退 | `src/control/agent_runtime.py`、`src/control/workflow_manager.py` |
| 分析层 | Python + subprocess Rscript | `src/analysis/sc_*.py`、`src/analysis/r_executor.py` |
| 知识库 | LightRAG + PostgreSQL/pgvector，Ollama embedding | `src/knowledge/lightrag_client.py`、`src/knowledge/async_runtime.py` |
| 公共证据 | 独立 FastAPI 服务 + SQLite | `src/public_api/app.py` |
| 文献 ETL | 独立 Python 子项目 | `pubmed-etl/main.py`、`sync_pubmed.py` |

## 关键约束

- **单进程本地原型**：Web 服务不是多用户生产服务；公共证据服务单独部署。
- **人工审批**：受控单细胞每个阶段都需要明确授权；审批依据绑定输入、产物和身份。
- **证据可追溯**：结果、脚本、日志、metadata、快照和记录围绕 run/stage 身份关联。
- **本地优先**：原始数据、项目记录和知识库留在本机；公共证据只上传允许的聚合字段。
- **共享知识库运行时**：所有 PG 后端 LightRAG 实例共用一个事件循环，见
  [知识库](knowledge-base.md)。
- **前端单一判定面**：五阶段授权/复核由 `web/src/lib/stageGate.ts` 统一判定，
  组件不另写一套。

## 入口

| 入口 | 说明 |
|---|---|
| `python -m src.api` | 构建 agent、启动 uvicorn、延迟打开浏览器；存在 `web/dist/` 时挂载前端 |
| `python -m src.main` | CLI 主类 `CellSpatioAgent`；交互式确认只在 Web 提供 |
| `python -m src.public_api --root <data>` | 公共证据服务，默认 127.0.0.1:8700 |
| `python -m src.cli.replay <run_id>` | 读取已有快照并恢复依赖，不重跑分析 |
| `python -m src.cli.records export ...` | 导出会话/项目记录与产物清单 |

更细的模块职责见[模块地图](modules.md)，跨层调用链见[关键流程](flows.md)。
