# 快速开始

[← 返回开发文档目录](README.md)

本页覆盖从源码启动、配置和调试。便携客户端的使用见 `README.md` 与
[使用指南](../user/usage_guide.md)。

## 环境要求

| 组件 | 要求 | 说明 |
|---|---|---|
| Python | `>=3.10` | CI 使用 3.12，便携客户端内置 3.13 |
| Node | 24 | 与 CI 一致；前端构建用 |
| R | 4.6.1 | 受控单细胞固定版本，见下 |
| PostgreSQL | 仓库内嵌二进制 | 端口 5433，不用 Docker |
| Ollama | 本地服务 | 知识问答与知识库导入需要 |

受控单细胞执行前会校验以下 R 包版本，不满足直接拒绝：

| 包 | 版本 |
|---|---|
| R | 4.6.1 |
| Seurat | 5.6.0 |
| SeuratObject | 5.4.0 |
| Matrix | 1.7.5 |
| irlba | 2.4.1 |

另外需要 `presto` 与 `uwot`。`Rscript` 在 `PATH` 中，或在 `.env` 设置 `R_HOME`。
构建 Windows 内置运行时包时，用 `client/build.ps1 -RRuntime <R目录>` 指定 R。

## 初始化

Windows 一键初始化：

```powershell
setup.bat
```

脚本会创建 `venv/`、安装 Python 依赖、创建数据目录并复制 `.env.example`；不安装 R 包，
也不构建前端。

手动初始化：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e ".[dev]"
```

前端首次构建或依赖变化后：

```powershell
cd web
npm ci
npm run build
```

构建产物输出到 `web/dist/`，`src/api/app.py` 在目录存在时把它挂载到 `/`。

## 配置

配置从 `.env` 读取（`src/config.py` 在导入时调用 `load_dotenv()`）。`.env` 不提交，
完整键与注释见 `.env.example`。

| 键 | 作用 | 默认 |
|---|---|---|
| `AGENT_LLM_PROVIDER` / `LLM_PROVIDER` | 主项目供应商：`deepseek` / `openai` / `zhipu` | `zhipu` |
| `ETL_LLM_PROVIDER` | 文献 ETL 供应商 | `deepseek` |
| `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` / `OPENAI_API_KEY` | 对应密钥 | 空 |
| `LLM_MAX_TOKENS` / `LLM_TIMEOUT` | 生成上限与超时 | `8192` / `120` |
| `AGENT_TOOL_RESULT_MAX_CHARS` / `AGENT_TOOL_RESULT_BUDGET_CHARS` | 单次工具结果上限 / 单轮累计预算 | `20000` / `60000` |
| `AGENT_KNOWLEDGE_TOOL` / `AGENT_KNOWLEDGE_TIMEOUT` | 文献证据工具开关（默认开启；`legacy`/`off`/`false` 回退已弃用的 `query_knowledge`，删除属 BREAKING）/ 检索超时秒数 | 开启 / `60` |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DATABASE` | PG 连接 | `127.0.0.1` / `5433` / `rag` |
| `OLLAMA_URL` / `EMBEDDING_MODEL` | Ollama 地址与 embedding 模型 | `http://localhost:11434` / `bge-m3` |
| `CELLSPATIO_LOCAL` | 便携客户端本地模式 | 关闭 |
| `CELLSPATIO_LOCAL_KG` | 文件型 LightRAG | 关闭 |
| `KB_AUTO_INGEST` | 自动补库 | 关闭 |
| `CELLSPATIO_PUBLIC_HOST` / `CELLSPATIO_PUBLIC_PORT` | 公共证据服务 | `127.0.0.1` / `8700` |

新增配置项时同时更新 `src/config.py`、`.env.example`，并在本页或
[使用指南](../user/usage_guide.md) 中说明用户可见行为。

## 启动

```powershell
# 后端 + 已构建前端，默认 127.0.0.1:8600，启动后自动开浏览器
python -m src.api

# CLI；交互式确认只在 Web 界面提供
python -m src.main

# 公共证据服务（独立进程）
python -m src.public_api --root <data>
```

PostgreSQL：

```powershell
.\start_pg.ps1
.\start_pg.ps1 -Stop
```

内嵌 PG 使用 `runtime/pgsql/` 和 `runtime/pgdata/`，端口固定 5433；默认 5432 留给本机
其他实例。测试库为 `rag_test`，由 `tests/conftest.py` 重定向。

## 前端开发

- `web/vite.config.ts` 当前没有配置 `/api` 开发代理，源码中的请求使用相对路径。
  直接运行 `npm run dev` 只能做纯 UI 调试，无法直接联调 :8600 后端。
- 联调与验收以 `npm run build` 后由 `python -m src.api` 挂载 `web/dist/` 为准。
- 纯前端逻辑用 `npm run test`（vitest，jsdom）覆盖。`package.json` 保留了
  `test:e2e` 脚本，但当前工作树没有 Playwright 配置与用例，联调仍以构建后挂载为准。

## 便携客户端

`client/launch.ps1` 设置 `CELLSPATIO_LOCAL=1`、固定 loopback，并把数据目录切到 `data/`
或 `CELLSPATIO_HOME`。发行包构建仅按用户明确要求运行，不要在普通开发流程中重建
`dist/client/`。
