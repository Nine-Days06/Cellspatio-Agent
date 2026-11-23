# 常见任务

[← 返回开发文档目录](README.md)

## 新增 API 路由

1. 在 `src/api/routes_*.py` 增加路由，状态变化落到 `src/ui/` 或 `src/analysis/`。
2. 在 `src/api/app.py` 注册 router；需要 store 时通过 `src/api/deps.py`，不要新建全局实例。
3. 更新 `web/src/lib/types.ts` 与对应 `api.ts`/组件。
4. 补单测；跨层字段补契约测试。

## 新增受控分析阶段

1. 在 `src/stage_defs.py` 增加阶段定义（产物目录、失败阶段）。
2. 在 `src/ui/sc_stage_defs.py` 注册运行态、互斥、中断与产物目录。
3. 在 `src/analysis/` 实现固定工具，复用 `stage_run.py` 与 `evidence.py` 的收尾与身份逻辑。
4. 在 `src/api/` 增加路由，复用 `stage_lookup.py`、`authorized_run.py`、`errors.py`。
5. 在 `src/ui/sc_stages.py`、`stage_lifecycle.py` 接入领取/完成/复核。
6. 更新 `web/src/lib/stageGate.ts`、`stageActivity.ts`、`types.ts` 和阶段组件。
7. 补单测与对应 `tests/integration/test_sc_*.py`。

## 修改确认卡

同时改：

- `src/ui/confirm_protocol.py`
- `src/ui/confirm_flow.py`
- `web/src/lib/confirmState.ts`

再跑 `tests/unit/test_confirm_contract.py`。确认请求必须重新核验会话、脚本、参数和绑定。

## 新增配置项

1. 在 `src/config.py` 读取并暴露配置。
2. 在 `.env.example` 增加带注释的示例。
3. 用户可见行为写入 [快速开始](getting-started.md) 或
   [使用指南](../user/usage_guide.md)。
4. 补对应单测；涉及默认值时不要只测显式设置分支。

## 修改知识库运行时

所有 PG 后端 LightRAG 实例共用 `src/knowledge/async_runtime.py` 的模块单例事件循环；
不要为实例单独 `asyncio.new_event_loop()`，也不要在运行时线程内再调用阻塞的
`runtime.run(...)`。PG 存储的 workspace 必须稳定、互异；省略会污染主库、方法库和 KG 记忆。
详见[知识库](knowledge-base.md)。

## 修改前端字段

1. 改后端响应与 `web/src/lib/types.ts`。
2. 改对应组件/状态。
3. 跑契约测试与 `npm run typecheck`、`npm run test`。

## 提交前检查

- [ ] 受影响测试已跑；PG/R 用例没有被跳过。
- [ ] `ruff check` 与 `ruff format --check` 通过。
- [ ] 改动 `web/` 时 typecheck/test/build 通过。
- [ ] 行为、结构或流程变化已同步对应文档，见[文档维护](documentation.md)。
- [ ] 没有提交 `.env`、`data/`、`knowledge_base/`、`snapshots/` 或本地 `docs/agents/`。
- [ ] 任何 `git push` 前已获得用户明确同意。
