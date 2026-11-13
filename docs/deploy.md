# 公共证据服务部署骨架

本文件把 `python -m src.public_api --root <data>` 部署为可内网小规模运行的服务。
首版只支持单进程、单机 SQLite；不做公网匿名访问、自动扩容或 Kubernetes。

## 拓扑

```text
浏览器 / CLI
  --HTTPS--> Caddy（443，TLS，1 MB 请求体上限，安全头）
    --HTTP--> uvicorn 127.0.0.1:8700（src.public_api）
      --> <data>/public_evidence/governance.db + cards/
      --> <data>/public_evidence/accounts.db
```

- uvicorn 只绑定回环地址，明文端口不对外。
- Caddy 终止 TLS；内网自签用 `tls internal`，正式域名换成真实证书。
- 单进程运行：SQLite 与内存登录限速不支持多 worker 横向扩展。

## 目录与权限

```text
/opt/cellspatio/                    代码与 .venv
/etc/cellspatio/public-api.env      环境变量（0640，仅服务账号可读）
/srv/cellspatio/data/public_evidence/
  governance.db                     提交、状态与审计事件
  accounts.db                       Argon2 账号与 token 哈希
  cards/                            不可变证据卡副本
/var/backups/cellspatio/            备份与审计导出
```

- 服务账号只对自己的数据根有写权限；`.env` 不放进仓库。
- `public_evidence/` 含账号库与 token 哈希，不提交、不打包、不公开。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CELLSPATIO_PUBLIC_DATA_ROOT` | 当前目录 | 数据根；等价于 `--root` |
| `CELLSPATIO_PUBLIC_HOST` | `127.0.0.1` | 上游监听地址，保持回环 |
| `CELLSPATIO_PUBLIC_PORT` | `8700` | 上游端口 |
| `CELLSPATIO_EVIDENCE_SESSION_DAYS` | `7` | Bearer token 有效期 |
| `CELLSPATIO_TRUST_PROXY` | `0` | 只有代理同机且上游只绑回环时设 `1` |

`CELLSPATIO_TRUST_PROXY=1` 时应用只读 `X-Forwarded-For` 最后一段；不读
`X-Real-IP`。Caddy 参考配置同时删除客户端自带的 `X-Real-IP`，双重避免伪造
转发头绕过登录限速。

## 首次部署（Linux 示例）

```bash
sudo useradd --system --home /opt/cellspatio --shell /usr/sbin/nologin cellspatio
sudo mkdir -p /opt/cellspatio /srv/cellspatio/data /etc/cellspatio /var/backups/cellspatio
sudo chown -R cellspatio:cellspatio /opt/cellspatio /srv/cellspatio/data /var/backups/cellspatio

# 代码放到 /opt/cellspatio 后安装依赖
cd /opt/cellspatio
python3 -m venv .venv
.venv/bin/pip install -e .
```

写入 `/etc/cellspatio/public-api.env`：

```text
CELLSPATIO_PUBLIC_DATA_ROOT=/srv/cellspatio/data
CELLSPATIO_PUBLIC_HOST=127.0.0.1
CELLSPATIO_PUBLIC_PORT=8700
CELLSPATIO_TRUST_PROXY=1
```

```bash
sudo chown root:cellspatio /etc/cellspatio/public-api.env
sudo chmod 0640 /etc/cellspatio/public-api.env

sudo cp docs/deploy/cellspatio-public.service.example /etc/systemd/system/cellspatio-public.service
sudo systemctl daemon-reload
sudo systemctl enable --now cellspatio-public

sudo cp docs/deploy/Caddyfile.example /etc/caddy/Caddyfile
# request_body 需要 Caddy >= 2.10；先校验再 reload：
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

内网自签证书的根 CA 需要分发给客户端；临时验收可用 `curl -k`。若用域名，先在客户端
hosts 文件里把 `cellspatio.internal` 指到服务器。参考配置用 `skip_install_trust`
避免部署时自动改系统信任库；需要系统级信任时再手动执行 `caddy trust`，
根证书在 Caddy 数据目录的 `pki/authorities/local/root.crt`。
systemd 单元不要在 `ExecStart` 里再写 `--root`，否则会覆盖
`CELLSPATIO_PUBLIC_DATA_ROOT`。

## 账号开户 runbook

```bash
sudo -u cellspatio -H /opt/cellspatio/.venv/bin/python -m src.cli.evidence_accounts \
  --root /srv/cellspatio/data create alice --role contributor

sudo -u cellspatio -H /opt/cellspatio/.venv/bin/python -m src.cli.evidence_accounts \
  --root /srv/cellspatio/data create dora --role domain_reviewer

sudo -u cellspatio -H /opt/cellspatio/.venv/bin/python -m src.cli.evidence_accounts \
  --root /srv/cellspatio/data create carl --role compliance_reviewer
```

- 复核者保持单角色；双角色账号目前只出领域队列。
- `roles <user> <role...>` 整体替换角色；复核者保持单角色，例如
  `roles dora domain_reviewer`。
- `list` 只显示 id、用户名、状态与角色，不显示密码哈希。
- `passwd <user>` 改密并吊销该账号全部 token；`disable <user>` 禁用并吊销 token；
  `enable <user>` 恢复。
- 忘记密码只能由运维重置，不提供自助找回。

## 启动、停止、升级与回滚

```bash
sudo systemctl start cellspatio-public
sudo systemctl stop cellspatio-public
sudo systemctl restart cellspatio-public
sudo journalctl -u cellspatio-public -f
```

升级前先备份数据；升级代码或依赖后重启服务，再跑一次 HTTPS 冒烟。回滚时停服，
恢复上一版代码与 `.venv`，数据用备份恢复（本片没有数据库 schema 迁移）。

## 备份与恢复

备份使用 SQLite backup API，兼容 WAL；`cards/` 是不可变卡副本，一并复制。
服务运行中备份数据库是安全的，但复制卡期间可能有新卡写入；正式备份建议停服或低峰执行。

```bash
sudo systemctl stop cellspatio-public
sudo -u cellspatio -H /opt/cellspatio/.venv/bin/python -m src.cli.evidence_backup \
  backup --root /srv/cellspatio/data --out /var/backups/cellspatio/2026-11-13
sudo systemctl start cellspatio-public
```

恢复前必须停服；目标 `public_evidence/` 必须为空。备份要求
`governance.db` 与 `accounts.db` 同时存在，缺一个就拒绝备份，避免恢复后账号丢失。
恢复到原数据根（先把旧库移走）：

```bash
sudo systemctl stop cellspatio-public
sudo mv /srv/cellspatio/data/public_evidence /srv/cellspatio/data/public_evidence.broken
sudo -u cellspatio -H /opt/cellspatio/.venv/bin/python -m src.cli.evidence_backup \
  restore --backup /var/backups/cellspatio/2026-11-13 --root /srv/cellspatio/data
sudo systemctl start cellspatio-public
```

也可以恢复到全新数据根，然后修改 `/etc/cellspatio/public-api.env` 的
`CELLSPATIO_PUBLIC_DATA_ROOT` 并重启服务；不要在 systemd 里硬编码另一条路径。

恢复后验证：账号可登录、accepted 证据可检索、citation 可读、事件完整。备份目录里的
`manifest.json` 记录数据库 SHA-256、卡数量与卡内容聚合摘要；任何一项不符时恢复会拒绝。

## 审计导出

```bash
sudo -u cellspatio -H /opt/cellspatio/.venv/bin/python -m src.cli.evidence_governance \
  --root /srv/cellspatio/data audit --out /var/backups/cellspatio/audit-2026-11-13.jsonl
```

- 一行一个 JSON 对象，`type` 为 `submission` 或 `event`。
- 只读 `governance.db`，不读取 `accounts.db`，因此不含密码哈希或 token 哈希。
- 条数等于 `submissions` 行数加 `events` 行数：

```bash
python3 - <<'PY'
import json
import sqlite3

path = "/srv/cellspatio/data/public_evidence/governance.db"
with sqlite3.connect(path) as conn:
    expected = (
        conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        + conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    )
records = sum(1 for line in open("/var/backups/cellspatio/audit-2026-11-13.jsonl") if line.strip())
assert records == expected, (records, expected)
print("audit records:", records)
PY
```

## 安全验收

```bash
# HTTPS 页面与安全头
curl -k -I https://cellspatio.internal/review/

# 1.1 MB 请求体应由代理返回 413
python3 -c 'open("/tmp/big.json","w").write("{\"x\":\"" + "a"*1100000 + "\"}")'
curl -k -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' --data-binary @/tmp/big.json \
  https://cellspatio.internal/api/auth/login

# 连续失败登录应触发 429；伪造 X-Real-IP 不改变限速键
for i in 1 2 3 4 5 6; do
  curl -k -o /dev/null -w '%{http_code}\n' -X POST \
    -H 'Content-Type: application/json' -H 'X-Real-IP: 203.0.113.9' \
    -d '{"username":"alice","password":"wrong"}' \
    https://cellspatio.internal/api/auth/login
done
```

预期：`Content-Security-Policy`、`X-Content-Type-Options`、`X-Frame-Options`、
`Referrer-Policy` 存在；HTTPS 响应含 `Strict-Transport-Security`；1.1 MB 请求返回 413；
第 6 次失败登录返回 429。浏览器验收再走一遍登录 → 上传 → 双签 → search/citation。

## 已知边界

- 登录限速在进程内存里，重启清零；长期防护仍靠代理/WAF。
- 单进程；不要加 uvicorn workers，SQLite 与内存限速不会共享正确状态。
- `request_body` 需要 Caddy >= 2.10；升级 Caddy 后先用 `caddy validate` 校验配置。
- 不做公网匿名访问、自动扩容、Kubernetes、邮件通知。
- 本片不修改治理状态机、证据卡 schema、双签规则与检索语义。
