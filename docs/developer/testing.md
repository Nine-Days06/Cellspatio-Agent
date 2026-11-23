# 测试与门禁

[← 返回开发文档目录](README.md)

按改动范围选择最小充分集合，提交前再跑完整门禁。完整分支、PR 与发版要求见
[协作约定](../../CONTRIBUTING.md)。

## 测试分层

| 目录 | 内容 |
|---|---|
| `tests/unit/` | 路由、存储、契约、前端无关的 Python 单测 |
| `tests/integration/` | API 冒烟、工作流、文献导入、R/Seurat 受控阶段 |
| `tests/performance/` | 性能相关用例 |
| `pubmed-etl/tests/` | 文献 ETL 子项目测试 |
| `web/src/**/*.test.ts(x)` | vitest 前端单测 |

`pytest` 的 `testpaths` 为 `tests`；`pg` marker 表示需要本地 PostgreSQL，
`tests/conftest.py` 会把连接重定向到 `rag_test`。

## 命令

Python 单测（推荐）：

```powershell
.\run_unit_tests.ps1
```

脚本把 pytest 临时目录放到项目内，按需启动内嵌 PG，并只关闭自己启动的实例；
同时清理会干扰测试内 `git init` 的 `GIT_CONFIG_*` 环境变量。

手动等价命令：

```powershell
.\start_pg.ps1
python -m pytest tests/unit/ -q --basetemp .scratch/pytest-unit
```

PG 用例被跳过不算通过：确认 `.\start_pg.ps1` 已运行，测试库为 `rag_test`。

R 集成测试按受影响阶段逐个传入文件（PowerShell 不会替 pytest 展开通配符）：

```powershell
python -m pytest tests/integration/test_sc_qc.py tests/integration/test_sc_filter.py `
  tests/integration/test_sc_pca.py tests/integration/test_sc_cluster.py `
  tests/integration/test_sc_markers.py -q
```

前端：

```powershell
cd web
npm run typecheck
npm run test
npm run build
```

## 改动范围对照

| 改动范围 | 必须运行 |
|---|---|
| Python 单测相关 | `.\run_unit_tests.ps1` |
| PostgreSQL 相关 | 启动 PG 后跑 `tests/unit/`；跳过不算通过 |
| R / Seurat 链路 | 受影响的 `tests/integration/test_sc_*.py` |
| API 跨层契约 | `test_stage_wire_contract.py`、`test_confirm_contract.py` |
| `web/` | typecheck、test、build |
| 提交前完整门禁 | [协作约定](../../CONTRIBUTING.md) 的“本地门禁” |

## CI

`.github/workflows/ci.yml` 在 Ubuntu 上运行：

- Python 3.12 + `pgvector/pgvector:pg16`（端口 5433）
- `ruff check`、`ruff format --check`
- `pytest tests/unit/ pubmed-etl/tests -q`
- Node 24：`npm ci`、`npm run typecheck`、`npm run test`、`npm run build`

CI 不替代本地 R 集成测试；修改 R/Seurat 链路必须在本地跑对应集成用例。

## 写测试时

- 新行为优先补最小单测；跨层字段同时补契约测试。
- PG 用例使用唯一 workspace/run_id，避免持久化状态导致去重跳过。
- 测试按需在用例内导入被测模块（`tests/**` 已在 ruff 规则中豁免 `E402`）。
