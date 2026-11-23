# 文档维护

[← 返回开发文档目录](README.md)

## 文档分工

| 文档 | 面向 | 何时更新 | 是否提交 |
|---|---|---|---|
| `README.md` | 用户与首次接触者 | 定位、安装方式、主要能力、入口变化 | 是 |
| `docs/user/usage_guide.md` | 用户 | 操作步骤、界面行为、配置的用户可见变化 | 是 |
| `docs/developer/` | 开发者与 agent | 架构、模块入口、环境、测试门禁、常见任务 | 是 |
| `CONTRIBUTING.md` | 贡献者 | 分支、提交、PR、发版和本地门禁 | 是 |
| `docs/api/inter_project_interface.md` | 主项目与 ETL | 跨项目数据结构或命令契约 | 是 |
| `docs/deploy.md` | 运维 | 公共证据服务部署、TLS、备份与审计 | 是 |
| `docs/agents/*` | 本地 agent | 代码地图、排障、Issue 与领域约定 | 否 |
| `docs/superpowers/*` | 本地规划 | 设计与实施草稿 | 否 |
| `GLOSSARY.md` / ADR | 领域与架构 | 术语或架构决策变化 | 是 |

## 更新规则

- 行为、结构或流程改动导致任一文档失真时，在同一改动中同步更新受影响内容，
  不做无关重写。
- 本目录按主题分页；新增页面时同时更新 [首页](README.md) 的导航表。
- 不把本地 `docs/agents/` 内容复制到可提交文档；本地细节保留在原处。
- 不在多个页面重复同一张符号表；模块入口只在[模块地图](modules.md)维护，
  其余页面链接过去。

## 本地文档检查

新增本地参考文件放 `docs/agents/`，并确认排除生效且未被跟踪：

```powershell
git check-ignore -v docs/agents/your-file.md
git ls-files docs/agents
```

第二条命令应无输出。根 `AGENTS.md` 同样被 `.git/info/exclude` 排除。

## 写作约定

- 用中文写说明，命令、路径、类型和配置键保持原文。
- 能用一个表或一段代码说清的，不写长篇叙述。
- 记录“为什么”和不能破坏的约束，不复制可从代码直接读出的细节。
- 跨页引用用相对 Markdown 链接；外部规范用官方一手链接。
