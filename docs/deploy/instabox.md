# CellSpatio InstaBox 部署

部署在 InstaBox 的 Linux 机器 `cellspatio` 上，应用本体不放进容器；R 包版本校验依赖
`Rscript` 精确版本，原生路径更可控。

## 拓扑

- 代码：`/data/home/Multiomics-Agent`，Python venv `.venv`，前端 `web/dist`。
- 应用：systemd `cellspatio-api.service`，监听 `127.0.0.1:8600`。
- 入口：nginx `127.0.0.1:8080` → `127.0.0.1:8600`；公网端口由 `instabox port publish` 暴露。
- 数据：SQLite `/data/cellspatio/sessions.db`；PG 17 `5433`；Ollama `11434`；magpie `3425`。
- 临时工作区：`/data/home/Multiomics-Agent/data/workspaces/<workspace_id>`，24 小时到期。

应用无内建 TLS，TLS 由 InstaBox 入口终止；nginx 必须转发
`X-Forwarded-Proto`，否则 Secure cookie 判定会失效。

## nginx

```nginx
server {
    listen 8080;
    client_max_body_size 101m;

    location / {
        proxy_pass http://127.0.0.1:8600;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }

    location ~ ^/api/sessions/[^/]+/local-input$ {
        return 403;
    }
}
```

`client_max_body_size` 略高于单文件 100 MB；解压后 300 MB 与累计配额由应用返回结构化 413。

## 更新代码

```bash
cd /data/home/Multiomics-Agent
git fetch origin
git merge --ff-only origin/dev
uv pip install --python .venv/bin/python -r /data/deploy/requirements.lock
cd web && npm run build && cd ..
sudo systemctl restart cellspatio-api
```

首次安装依赖、R、PG 与 Ollama 的完整记录见本机 `docs/agents/deployment.md`。

## 管理账户

```bash
sudo -u user bash -lc 'set -a; . /etc/cellspatio/cellspatio.env; set +a;
  cd /data/home/Multiomics-Agent;
  .venv/bin/python -m src.cli.accounts --root . init-three --yes'
```

初始密码只输出一次，部署时写入 `/data/deploy/admin_credentials.txt`（`0600`），不进入仓库。
改密/禁用/列表分别用 `passwd`、`disable`、`list` 子命令。

## 到期清理

```bash
.venv/bin/python -m src.cli.workspaces cleanup --root /data/home/Multiomics-Agent --yes
```

只删过期 `ephemeral` 工作区；受控阶段运行中或最近 2 小时仍有请求的工作区跳过，并清理
对应 `.wrroc/<run_id>`。生产环境用 systemd timer 每 15 分钟触发，手动预演加 `--dry-run`。

## 回滚

- SQLite：恢复 `/data/cellspatio/sessions.db.bak-<日期>` 后重启应用。
- nginx：恢复 `/etc/nginx/sites-available/cellspatio.bak-<日期>` 后 `nginx -t && systemctl reload nginx`。
- 代码：`git checkout <上一个验收提交>`，重新 `uv pip install` 并 `npm run build`，再重启服务。
- 公网入口：`instabox port unpublish cellspatio 8080`。

## 验收基线

- 首页、`/login`、`/workspace` 与静态资源均 200，不带 `WWW-Authenticate`。
- 两个访客的工作区、会话与项目互不可见；跨工作区阶段接口返回 404。
- PBMC3k（2700 细胞、32738 基因）上传 zip 后 QC 成功，过滤后 2638 细胞，PCA/维度选择/聚类/marker 全链路完成。
- 过期临时工作区访问 `/api/*` 返回 410 `workspace_expired`，清理任务删除文件与记录。
- 超过 100 MB 的单文件由 nginx 或应用返回 413。

## 约束

- InstaBox free plan 单次运行 120 分钟起、最多 480 分钟，空闲 30 分钟自动 suspend，不是 24/7 服务。
- 分析必须由 `Rscript` 执行；不要把 R 分析搬进容器后绕过版本校验。
- 公网入口不做 Basic Auth；账号、访客 cookie 与应用内 410/413 是唯一访问控制。
