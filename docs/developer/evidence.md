# 证据与治理

[← 返回开发文档目录](README.md)

本页区分本地证据卡、公共证据治理和公共证据服务。原始数据与本地记录不离开本机。

## 本地证据卡

| 路径 | 职责 |
|---|---|
| `src/evidence/schema.py` | 证据卡结构与允许字段 |
| `src/evidence/builder.py` | 按人工 gate 构建 qc/cluster/pca/marker 卡 |
| `src/cli/evidence.py` | 导出与复核入口 |

证据卡只保留允许的聚合字段与绑定信息，禁止 barcode、坐标、样本 ID 和绝对路径。
marker 计算不自动命名细胞类型；未复核结果不得宣称已复核。

## 公共证据治理

| 路径 | 职责 |
|---|---|
| `src/evidence/governance.py` | SQLite + 不可变卡、准入/双签/修订/撤销 |
| `src/evidence/review_protocol.py` | 复核/撤回规则的纯协议 |
| `src/evidence/contribution.py` | 贡献预览与提交 |
| `src/evidence/vocabulary.py` | gate/状态/动作/角色/清单中文标签的唯一来源 |
| `src/evidence/accounts.py` | 独立账号库，Argon2 + Bearer token |
| `src/evidence/public_client.py` | 远程登录/上传/检索/引用 |

## 公共证据服务

```powershell
python -m src.public_api --root <data>
```

- 默认监听 `127.0.0.1:8700`；静态复核/贡献者页面挂载在 `/review/`。
- 账号管理：

```powershell
python -m src.cli.evidence_accounts --root <data> create|list|passwd|roles|disable|enable
```

- 远端操作走 `evidence_governance` 的 login/upload/remote-search/citation。
- 治理失败统一抛 `EvidenceError(kind)`；HTTP 响应为 `{"detail", "code"}`，状态码映射在
  `src/public_api/app.py::_KIND_STATUS`，不要按错误文案推断。
- `/api/public/vocabulary` 免登录返回五组中文标签；缺键回退原始键。
- `GET /events` 同时返回 review 能力：角色、动作规格、阻断原因与清单键；页面只渲染服务端结果。

## 隐私与贡献边界

- 贡献前必须预览 `will_send` 和未发送字段，并获得显式同意。
- 不发送原始数据、barcode、坐标、样本 ID、绝对路径。
- 公共检索/引用只返回 `accepted` 证据。
- 公共证据部署、TLS、备份与审计见[部署说明](../deploy.md)。
