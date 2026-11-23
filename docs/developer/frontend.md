# 前端

[← 返回开发文档目录](README.md)

前端位于 `web/`，技术栈为 React 18 + Vite + TypeScript + Tailwind 4 + zustand。

## 目录

| 路径 | 职责 |
|---|---|
| `web/src/pages/ChatPage.tsx` | 对话主页面 |
| `web/src/components/` | 会话、确认卡、阶段复核、图表、Markdown 等交互组件 |
| `web/src/lib/http.ts` | JSON 与 SSE 共用的请求/错误解析层 |
| `web/src/lib/sse.ts` | `/api/chat` 流式事件解析 |
| `web/src/lib/api.ts` | 后端 API 封装 |
| `web/src/lib/types.ts` | 后端响应类型 |
| `web/src/lib/sessionWorkspace.ts` | 每会话首次 restore、历史接纳、激活边界与确认校验 |
| `web/src/lib/stageGate.ts` | 五阶段授权/复核的唯一判定面 |
| `web/src/lib/stageActivity.ts` | 自身/下游运行态推导 |
| `web/src/lib/useStageReview.ts` | generation、忙碌、轮询与父级刷新 |
| `web/src/lib/confirmState.ts` | 确认动作/状态常量与 `scriptStatusForAction` |
| `web/src/store/chat.ts` | 对话 zustand 状态；reset 统一切换/清空的 abort 与复位 |
| `web/src/components/ThemeToggle.tsx` | 根元素 `.dark`；首屏恢复 `cellspatio-theme` |

## 约定

- HTTP 请求统一走 `web/src/lib/http.ts`；网络错误归一为 `ApiError(0)`，非 2xx 使用后端
  `detail`。
- 后端顶层响应键与 `web/src/lib/types.ts` 由
  `tests/unit/test_stage_wire_contract.py` 交叉校验；改字段时两边一起改。
- 阶段授权/复核不要在组件里另写判断，统一用 `stageGate.ts`；运行态用 `stageActivity.ts`。
- 确认动作/状态必须与 `src/ui/confirm_protocol.py` 保持同步，跑
  `tests/unit/test_confirm_contract.py`。
- 切换会话/清空状态必须经过 store 的私有 reset，避免遗漏 abort 和复位。

## 构建与测试

```powershell
cd web
npm run typecheck
npm run test
npm run build
```

构建产物输出到 `web/dist/`。纯前端单测用 vitest（jsdom，只收集
`src/**/*.test.{ts,tsx}`）。`package.json` 保留了 `test:e2e` 脚本，但当前工作树没有
Playwright 配置与用例。

## 开发模式限制

`web/vite.config.ts` 当前没有配置 `/api` 开发代理，源码中的请求使用相对路径。
直接运行 `npm run dev` 只能做纯 UI 调试，无法直接联调 :8600 后端。联调与验收以
`npm run build` 后由 `python -m src.api` 挂载 `web/dist/` 为准。
