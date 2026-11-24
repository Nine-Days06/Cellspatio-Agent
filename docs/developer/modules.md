# 模块地图

[← 返回开发文档目录](README.md)

本页只维护模块级职责和边界；符号、调用关系和行号用 CodeGraph 核对。
本地 `docs/agents/code-map.md` 保留更细的入口与历史差异记录（不提交）。

| 路径 | 职责 |
|---|---|
| `src/api/` | FastAPI 路由、应用工厂、SSE、会话锁、工作区中间件、登录/上传与受控阶段 HTTP 映射 |
| `src/control/` | Agent 运行时、工具路由、技能、工作流、WRROC、快照、KG 记忆 |
| `src/analysis/` | 受控单细胞固定工具、动态 R 脚本执行、产物与复核校验 |
| `src/ui/` | SQLite 会话/工作区/账户存储、会话标题来源与自动命名（`session_title.py`）、确认流程、阶段生命周期、到期清理与占用管理 |
| `src/knowledge/` | LightRAG 封装、PG 存储、知识导入与问答、Ollama 运行时 |
| `src/evidence/` | 本地证据卡、公共证据治理、账号与远程客户端 |
| `src/public_api/` | 独立公共证据 HTTP 服务与无构建静态页 |
| `src/data/` | GEO/KEGG/UniProt/Reactome/HPA fetcher、基因映射、10x 校验 |
| `src/skills/` | 技能 Base/Manifest/Registry/Loader 与热重载 |
| `src/schemas/` | 工作流等共享数据结构 |
| `src/cli/` | 记录、回放、证据治理、工作区清理与管理账户初始化命令 |
| `src/stage_defs.py` | 阶段名、产物目录名、失败阶段的唯一来源 |
| `web/src/` | `pages` 页面、`lib` 传输/契约/阶段门禁、`store` zustand、`components` 交互 |
| `pubmed-etl/` | PubMed 下载、过滤、LLM 验证、导出 |
| `client/` | 便携客户端启动、引导与打包脚本 |

## 边界规则

- API 路由只做参数解析、锁区间和错误映射；状态变化落到 `src/ui/` 或 `src/analysis/`。
- 受控阶段的目录拼接、失败投影和运行态分别由 `src/stage_defs.py`、
  `src/ui/sc_stage_defs.py`、`src/api/stage_runtime.py` 统一，不在路由里另写。
- 四阶段路由不互相导入，定位逻辑集中在 `src/api/stage_lookup.py`。
- 阶段之间不导入下划线私有名；共享字节身份、输入指纹和 CSV 由
  `src/analysis/evidence.py` 提供。
- 工作区身份只在 `src/api/auth.py` 的中间件解析；路由通过 `workspace_context()` 读，
  不自行读 cookie 或建临时工作区。访客上传入口是 `routes_upload.py`，管理账户仍走
  `routes_input.py` 的本地路径。
- 前端与后端的顶层键由契约测试交叉校验，改字段时两边一起改。

## 跨项目边界

`pubmed-etl/` 与主项目通过批次文件和命令契约衔接，不直接共享运行时状态。
数据结构与命令见[接口规范](../api/inter_project_interface.md)。
