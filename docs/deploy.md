# 多用户认证部署

应用只监听本机 `127.0.0.1:8600`，由 systemd 托管、nginx 终止 TLS 并转发。

## Linux 上线顺序

```bash
cd /opt/cellspatio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd web && npm ci && npm run build && cd ..
cp .env.example .env  # 编辑并填写实际配置
.venv/bin/python -m src.cli.users create <username> [--admin]
sudo systemctl enable --now cellspatio
sudo nginx -t && sudo systemctl reload nginx
```

`CELLSPATIO_COOKIE_SECURE=1` 与 `CELLSPATIO_SITE_URL` 必须成对配置：前者启用
但后者为空时应用拒绝启动，因为 Origin 校验会随之关闭。

升级旧库时，已有会话的 `owner_id` 为 `NULL`，会全部返回 404；需用
`python -m src.cli.users list` 查到账号 ID 后，在默认 `.cellspatio/sessions.db`
（或 `CELLSPATIO_SESSIONS_DB` 指向的库）手工执行：

```sql
UPDATE sessions SET owner_id = '<user-id>' WHERE owner_id IS NULL;
```

## systemd

将路径、运行用户和端口按实际环境替换。当前仓库没有可直接导入的模块级 ASGI
app，`python -m src.api` 内部会用 uvicorn 绑定 `127.0.0.1:8600`。
应用启动时由 `src/config.py` 的 `load_dotenv` 读取 `.env`，无需在 systemd 里
逐项写 `Environment=`。

```ini
[Unit]
Description=CellSpatio
After=network.target

[Service]
User=cellspatio
WorkingDirectory=/opt/cellspatio
EnvironmentFile=/opt/cellspatio/.env
ExecStart=/opt/cellspatio/.venv/bin/python -m src.api
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## nginx

`proxy_buffering off`、`proxy_cache off` 和 `X-Accel-Buffering no` 确保 SSE 不被
缓冲；`proxy_read_timeout 3600s` 覆盖长任务。证书路径为占位，替换为真实证书。

```nginx
server {
    listen 443 ssl http2;
    server_name cellspatio.example.com;

    ssl_certificate     /etc/letsencrypt/live/cellspatio.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cellspatio.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8600;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        add_header X-Accel-Buffering no;
    }
}
```
