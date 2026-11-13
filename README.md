# CellSpatio

面向实验研究者的单细胞与空间/时序组学分析与知识问答助手。用自然语言描述数据和目标，
系统负责检索公共数据、组织受控分析、记录证据，并在回答中给出可追溯来源。

> 当前定位是本地/单机研究原型。分析结果需要人工复核；聚类和 marker 是探索性结果，
> 不自动命名细胞类型。

## 你能做什么

- **单细胞分析**：10x MTX 目录或 counts CSV 输入，受控 QC → 过滤 → PCA → 聚类/UMAP
  → marker；每个阶段都需要明确授权。
- **bulk 与空间分析**：差异表达和空间转录组脚本生成，人工确认后执行；失败最多自动修复 2 次。
- **知识问答**：检索文献片段，回答附 PMID/DOI、证据编号和“事实/推断”标记。
- **数据获取**：GEO 数据集检索与下载；KEGG/UniProt 条目进入知识流。
- **本地优先**：原始数据、项目记录和知识库留在本机；公共证据只上传聚合字段。
- **项目与会话**：侧栏管理项目、会话、归档、置顶和本地数据。

## 快速开始（Windows 便携客户端）

便携客户端有两种包：

| 包 | 适合谁 | 首次启动 |
|---|---|---|
| **在线安装包** | 已有 Python/R 环境 | 解压后运行 `bootstrap.ps1`，再运行 `launch.cmd` |
| **内置运行时包** | 不想安装 Python/R | 解压后直接运行 `launch.cmd` |

1. 解压到用户可写目录，不要放在 `Program Files`。
2. 在线安装包先运行 `bootstrap.ps1`；内置运行时包跳过这一步。
3. 双击 `launch.cmd`，浏览器打开 `http://127.0.0.1:8600`。
4. 数据、记录和知识库保存在便携目录的 `data/`。升级时覆盖解压，不要先删目录。

详细目录、升级和离线范围见包内 `README.txt`。

## 从源码运行

适合开发者和需要自定义环境的用户：

```powershell
git clone https://github.com/Nine-Days06/Cellspatio-Agent.git
cd Cellspatio-Agent
setup.bat
```

`setup.bat` 会创建 `venv`、安装 Python 依赖、创建数据目录并复制 `.env.example`；
不会自动安装 R 包，也不会构建前端。

```powershell
copy .env.example .env
# 编辑 .env，至少填写一个 LLM API Key
.\venv\Scripts\Activate.ps1
python -m src.api
```

- Web：`python -m src.api`，默认 `127.0.0.1:8600`，启动后自动打开浏览器。
- CLI：`python -m src.main`，输入 `quit` 或 `exit` 退出；交互式确认只在 Web 界面提供。
- 前端构建：首次从源码运行且没有 `web/dist/` 时，执行
  `cd web && npm ci && npm run build`。
- 受控单细胞固定版本要求：R 4.6.1、Seurat 5.6.0、SeuratObject 5.4.0、
  Matrix 1.7.5、irlba 2.4.1；其他版本会被拒绝执行。
- 知识问答需要 Ollama（默认 `bge-m3`）与一个 LLM 供应商；安装完成后，只做
  QC → marker 可以离线。

## 使用方式

1. 在侧栏新建项目或会话；项目可以绑定本地工作目录。
2. 在输入区选择「智能对话」或「知识问答」。
3. 分析请求会先给出脚本或候选数据确认卡；确认后才执行。
4. 单细胞按阶段展示 QC、过滤、PCA、聚类和 marker 证据；每步都需要人工授权。
5. 结果、证据和运行记录保存在本地 `data/` 与项目目录中。

## 当前边界

- 聚类是探索性结果，marker 结果默认未复核，不自动命名细胞类型。
- 受控单细胞不支持 H5/H5AD、Seurat 对象或 FASTQ；输入必须是 10x MTX 目录或 counts CSV。
- 单次 R 执行上限 300 秒；超时或失败不会自动重跑。
- 本地服务是单进程、单机原型，不是多用户生产服务；公共证据服务部署见
  [部署说明](docs/deploy.md)。
- Windows 便携客户端是当前主要分发方式；源码方式支持 Windows/macOS/Linux。

## 文档

- [使用指南](docs/user/usage_guide.md)：安装、配置、数据、分析、问答和限制。
- [部署说明](docs/deploy.md)：公共证据服务的 Caddy/TLS、备份与审计。
- [协作约定](CONTRIBUTING.md)：开发环境、测试门禁、分支与 PR。
- [文献 ETL](pubmed-etl/README.md)：PubMed 批量下载与清洗。

## 许可证

本项目暂未设置许可证，保留所有权利。使用、修改或分发前请联系维护者。
