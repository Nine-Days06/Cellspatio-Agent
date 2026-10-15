<!-- 标题请用 Conventional Commits 前缀：feat:/fix:/docs:/refactor:/test:/chore: -->

## 改了什么

<!-- 一句话概述本 PR 的改动 -->

## 为什么

<!-- 动机、背景；关联的 issue 路径，例如 .scratch/<feature>/issues/01-xxx.md -->

- 关联 issue：

## 怎么验证

<!-- 列出实际跑过的命令与结果，不要只写"测试通过" -->

- [ ] `ruff check src/ tests/ pubmed-etl/ *.py`
- [ ] `ruff format --check src/ tests/ pubmed-etl/ *.py`
- [ ] `pytest tests/unit/ -q`
- [ ] 前端门禁（若改 `web/`）：`npm run typecheck && npm run test && npm run build`

## 检查清单

- [ ] 单个 PR 只做一件事
- [ ] 已同步目标分支最新代码（`dev`，或 `main` 的 `hotfix`）
- [ ] 未提交本地专用内容（`.scratch/` `snapshot/` `.wrroc/` `.memory/` `data/` 等）
- [ ] 文档已随改动更新（如涉及接口/用法）
