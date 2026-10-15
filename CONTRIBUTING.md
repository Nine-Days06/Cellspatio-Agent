# 协作约定

本文件规定本仓库的多人协作流程。所有贡献者（含 AI agent）均须遵循。

主要依据（官方一手规范）：

- [GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow) — GitHub 官方轻量分支工作流
- [Git Flow（原始模型）](https://nvie.com/posts/a-successful-git-branching-model/) — Vincent Driessen 的 `develop`/`release`/`hotfix` 分支模型
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) — 提交信息规范
- [Semantic Versioning 2.0.0](https://semver.org/) — 版本号规范
- [Creating a pull request](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request) — PR 流程
- [git-fetch](https://git-scm.com/docs/git-fetch) / [git-pull](https://git-scm.com/docs/git-pull) — 拉取语义
- [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) — 分支保护

> 注：`fix/*` 分支、`snapshot/*` 命名空间、ruff 工具链与本地门禁命令为本项目自定义，非上述规范内容。

## 分支策略

以 GitHub Flow 为基线，叠加 `dev` 集成分支与 `release` 发布分支（源自 Git Flow），供后续版本化发布使用。

> 命名适配：Git Flow 原文用 `master`，本仓库遵循 GitHub 现行默认用 `main`，两者指同一角色。

| 分支 | 用途 | 从哪分出 | 合回哪 | 命名 |
|---|---|---|---|---|
| `main` | 稳定主干，只含已发布/可发布代码；**禁止直接提交** | — | — | — |
| `dev` | 集成分支，日常开发汇聚于此，是 `feature`/`fix` 的基线 | `main` | `main`（经 `release`） | — |
| `feature/<slug>` | 新功能，完成后 PR 合入 `dev` 并删除 | `dev` | `dev` | `feature/gene-id-unify` |
| `fix/<slug>` | 普通缺陷修复（本项目自定义，等价于小粒度 `feature`） | `dev` | `dev` | `fix/pg-loop-reuse` |
| `release/<version>` | 发布准备：冻结功能、仅修 bug、定版本号；完成后合入 `main` 并回灌 `dev` | `dev` | `main` + `dev` | `release/0.2.0` |
| `hotfix/<slug>` | 生产紧急修复，直接从 `main` 分出，修完合回 `main` 并回灌 `dev` | `main` | `main` + `dev` | `hotfix/kb-import-crash` |
| `snapshot/*` | ⚠️ **运行时保留命名空间**，由 `SnapshotManager` 自动创建用于溯源 | — | — | 不要手工提交或推送 |

说明：

- `main` 与 `dev` 均为长期分支；`feature`/`fix`/`release`/`hotfix` 为短生命周期分支，合并后删除。
- 轻量改动可走 `feature`/`fix` 直达 `dev`；`dev` 只接受合并，不直接提交（与 `main` 一致）。
- `release/<version>` 中的 `<version>` 遵循 [SemVer](https://semver.org/)（`MAJOR.MINOR.PATCH`）；版本号如何递增由提交类型决定，见下节。
- `hotfix` 例外：若当前存在未完成的 `release` 分支，hotfix 应合入该 `release` 分支而非 `dev`，随发布一并进入 `dev`（Git Flow 原始规则）。
- `snapshot/*` 是分析运行的可复现快照，只存本地、不推远程。请勿把 `snapshot/` 当作协作分支。

## 开发流程

```bash
# 1. 同步 dev（集成基线）
git fetch origin
git merge origin/dev

# 2. 从 dev 建分支
git checkout -b feature/your-feature

# 3. 开发 + 本地门禁（见下）
# 4. 提交（Conventional Commits）
git add <files>
git commit -m "feat: 你的功能描述"

# 5. 推送并开 PR（目标分支为 dev）
git push -u origin feature/your-feature
```

发布流程：

```bash
# 1. 从 dev 切发布分支，冻结功能
git checkout -b release/0.2.0 dev
# 2. 仅修 bug、改版本号（pyproject.toml / web/package.json）
# 3. 合入 main 并打标签（--no-ff 保留发布合并记录）
git checkout main && git merge --no-ff release/0.2.0
git tag -a v0.2.0 -m "Release 0.2.0"
# 4. 回灌 dev，保持不落后
git checkout dev && git merge --no-ff release/0.2.0
```

远程操作约定：

- 拉取用 `git fetch` + `git merge`，**不用 `git pull`**（`pull` = `fetch` + 自动整合，见 [git-pull 文档](https://git-scm.com/docs/git-pull)）。
- `main` / `dev` **永不 `git push -f`**；强制推送会覆盖他人提交（见 [分支保护](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)）。

## 提交信息：Conventional Commits

```
<type>[optional scope]: <description>
```

| type | 含义 | SemVer 影响 |
|---|---|---|
| `feat` | 新功能 | MINOR |
| `fix` | 缺陷修复 | PATCH |
| `docs` | 文档 | 无 |
| `refactor` | 重构（行为不变） | 无 |
| `test` | 测试 | 无 |
| `chore` | 构建/工具/杂项 | 无 |

- **scope（可选）**：括号内标注影响范围，如 `fix(parser): ...`。
- **BREAKING CHANGE**：破坏性变更须在 type/scope 后加 `!`，或在 footer 写 `BREAKING CHANGE: <说明>`，**对应 MAJOR**。这是 SemVer MAJOR 的唯一触发方式。

```
feat!: 移除旧版 API

BREAKING CHANGE: 配置文件改为仅接受 v2 格式
```

规范见 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)。

## Pull Request

1. 一个 PR 只做一件事。
2. 按 `.github/PULL_REQUEST_TEMPLATE.md` 填写：改了什么、为什么、关联 issue、验证证据。
3. 合并前须通过：lint + 单元测试 + 至少一次审查（CI 自动跑前两项）。
4. 合并后删除源分支。

## 本地门禁（提交前必跑）

```bash
# Python
ruff check src/ tests/ pubmed-etl/ *.py
ruff format --check src/ tests/ pubmed-etl/ *.py
pytest tests/unit/ -q

# 前端（改动 web/ 时）
cd web && npm run typecheck && npm run test && npm run build
```

格式化用 `ruff format` 统一，不再使用 black。

## 不提交、不推送的内容（本地专用）

以下内容已在 `.gitignore` / 全局排除中忽略，请勿提交：

- `.scratch/` — 本地 issue tracker 与 spec（mattpocock 工作流）
- `snapshot/` — 分析快照 worktree
- `.wrroc/` — 工作流溯源记录
- `.memory/` — agent 跨会话记忆
- `data/` `knowledge_base/` `pubmed-etl/data/` — 运行时数据与缓存
- `.env` — 密钥
- `opencode.json` — 本地 opencode 配置（已在全局 git 排除中忽略）
- `AGENTS.md` `docs/agents/` — 本地 agent 规则（已在 `.git/info/exclude` 忽略）
