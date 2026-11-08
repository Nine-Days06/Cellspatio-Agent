# ADR 0001：多用户认证与会话隔离

- 状态：已采纳（`multiuser-auth` 并行分支，永不合并回 `main`）
- 日期：2026-11-07

## 背景

服务将从本机单用户部署到公网服务器。当前 `sessions` 无用户归属、API 无鉴权，
`session_id` 等同隐式口令；任何拿到 UUID 的请求都能读取会话。

## 决策

1. 应用内做认证，不信任反代注入的身份头；反代只终止 TLS。
2. 密码用 argon2 哈希；登录成功后签发随机 token，服务端 `auth_tokens` 存
   SHA-256，cookie 为 `HttpOnly + SameSite=Lax`（生产加 `Secure`）。
3. 知识库全局共享；会话按 `sessions.owner_id` 隔离，不属于当前用户一律 404。
4. 本期手动开户（`python -m src.cli.users create`），后续再开放自注册。
5. 路径型 API 的会话归属在中间件统一校验；`/api/chat`、`/api/confirm`
   的 `session_id` 来自 body，在路由内校验。

## 部署约束

- systemd/nginx 部署见 [部署说明](../deploy.md)。
- 必设 `CELLSPATIO_COOKIE_SECURE=1` 与 `CELLSPATIO_SITE_URL=https://...`。
- 首个管理员：`CELLSPATIO_ADMIN_USER` / `CELLSPATIO_ADMIN_PASSWORD`（仅在用户表为空时建）。
- 登录限速是进程内内存实现，重启清零；长期防护交给反代 fail2ban。

## 已知限制

- 单进程 + 进程内 `session_lock`：多用户并发分析仍排队，多 worker 不可用。
- 未做管理员查看全部会话、未做审计日志、未做自注册与找回密码。
