# CellSpatio 开发文档

面向参与本仓库开发的工程师与 AI agent。用户安装和使用见
[README](../../README.md) 与
[使用指南](../user/usage_guide.md)；分支、提交和 PR 流程见
[协作约定](../../CONTRIBUTING.md)。

本页在 GitHub 上直接渲染，不需要启动任何服务；页面间的相对链接和表格都可直接查看。

每个页面顶部都有“返回开发文档目录”链接。新增页面时同时更新本页导航表。

## 从哪里开始

| 你的任务 | 先读 |
|---|---|
| 第一次跑起项目 | [快速开始](getting-started.md) |
| 理解分层和入口 | [架构](architecture.md) |
| 找一个模块或文件 | [模块地图](modules.md) |
| 改对话、确认卡或受控分析 | [关键流程](flows.md) |
| 改 React 页面或状态 | [前端](frontend.md) |
| 改知识库、检索或 Ollama | [知识库](knowledge-base.md) |
| 改证据卡或公共证据服务 | [证据与治理](evidence.md) |
| 写测试或跑提交门禁 | [测试与门禁](testing.md) |
| 做具体开发任务 | [常见任务](recipes.md) |
| 同步文档 | [文档维护](documentation.md) |
| 排查故障 | [排障](troubleshooting.md) |

## GitHub 阅读方式

1. 在仓库中打开 `docs/developer/README.md`。
2. 按上表进入主题页；每页顶部可返回本目录。
3. 需要全局搜索时用 GitHub 仓库搜索；本页导航表是主题入口。

## 文档边界

- `docs/developer/` 是可提交的开发者入口；本目录内容随代码一起维护。
- `docs/agents/` 是本地 agent 参考（代码地图、排障、Issue 约定），被 `.git/info/exclude`
  排除，不提交。
- `docs/superpowers/` 是本地规划与设计草稿，不提交。
- 符号级定位优先用 CodeGraph；本目录只记录跨模块事实，不维护完整符号表。

## 维护规则

行为、结构或流程改动导致任一页面失真时，在同一改动中同步更新受影响页面，不做无关重写。
各文档的职责划分见[文档维护](documentation.md)。

## 仓库入口

- [README](../../README.md)：项目定位、安装方式、用户能力。
- [使用指南](../user/usage_guide.md)：安装、配置、数据、分析和问答操作。
- [协作约定](../../CONTRIBUTING.md)：分支、提交、PR、本地门禁和发版。
- [接口规范](../api/inter_project_interface.md)：主项目与文献 ETL 的跨项目契约。
- [部署说明](../deploy.md)：公共证据服务的 Caddy/TLS、备份与审计。
- [InstaBox 部署](../deploy/instabox.md)：主站原生全栈、nginx、账户初始化与回滚。
- `GLOSSARY.md`：受控分析复核术语。
