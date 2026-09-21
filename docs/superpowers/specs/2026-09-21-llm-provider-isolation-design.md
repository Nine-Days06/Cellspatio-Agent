# LLM 供应商前缀隔离设计

> **目标**：让主项目（multiomics-agent）与 pubmed-etl 各自独立设置 LLM 供应商，避免共享同名环境变量 `LLM_PROVIDER`。

## 背景与问题

### 当前状况

- **两项目共用的环境变量名**：`LLM_PROVIDER`，都从 `load_dotenv()` 读取（默认从当前工作目录 `.env`）
- **配置结构**：`src/config.py` 与 `pubmed-etl/config/settings.py` 都有 `LLM_PROVIDER_CONFIGS` 字典（deepseek/openai/zhipu 三供应商）
- **加载位置**：主项目 `.env` 在仓库根；`pubmed-etl/.env` 不存在（pubmed-etl 是独立工具）

### 冲突点

当从根目录运行两个工具时，它们**共享同一个环境变量 `LLM_PROVIDER`**：
```bash
# .env 中设置
LLM_PROVIDER=zhipu

# 运行主项目
python -m src.ui.app       # → 主项目使用智谱

# 运行 pubmed-etl
python pubmed-etl/main.py  # → pubmed-etl 也使用智谱
```

无法实现：「pubmed-etl 用 deepseek，主项目用 zhipu」。

## 设计目标

1. **完全隔离**：两项目各自通过不同的环境变量名选择 LLM 供应商
2. **向后兼容**：保留 `LLM_PROVIDER` 作为别名，避免破坏现有配置
3. **最小改动**：不触及 `*_PROVIDER_CONFIGS` 字典结构，仅调整环境变量读取
4. **API 密钥不冲突**：`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY` 等保持通用名（两项目引用不同 provider 时天然不冲突）

## 方案设计

### 环境变量命名

| 项目 | 主变量 | 兼容别名 | 默认值 |
|---|---|---|---|
| 主项目（multiomics-agent） | `AGENT_LLM_PROVIDER` | `LLM_PROVIDER` | `deepseek` |
| pubmed-etl | `ETL_LLM_PROVIDER` | `LLM_PROVIDER` | `deepseek` |

### 模块内部变量名保持一致

两项目内部变量名**都保持 `LLM_PROVIDER`**，改动点仅在于：
- `src/config.py`：读取 `AGENT_LLM_PROVIDER` → `LLM_PROVIDER_CONFIGS`
- `pubmed-etl/config/settings.py`：读取 `ETL_LLM_PROVIDER` → `LLM_PROVIDER_CONFIGS`

### 读取逻辑

```python
# src/config.py（主项目）
LLM_PROVIDER = (
    os.environ.get("AGENT_LLM_PROVIDER")
    or os.environ.get("LLM_PROVIDER")
    or "deepseek"
)

# pubmed-etl/config/settings.py
LLM_PROVIDER = (
    os.environ.get("ETL_LLM_PROVIDER")
    or os.environ.get("LLM_PROVIDER")
    or "deepseek"
)
```

### API 密钥保持通用名

`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`OPENAI_API_KEY` **不加前缀**。

**理由**：两项目引用不同 provider 时密钥天然不冲突（deepseek vs zhipu 各用各的 key）。只有两项目将来需要「同一 provider 不同账号」时才需拆分——YAGNI，暂不做。

## 实施范围

### 修改的文件

1. `src/config.py`（主项目）
   - 修改 `LLM_PROVIDER` 读取逻辑（新增 `AGENT_LLM_PROVIDER` 优先）
   - 更新注释说明环境变量命名

2. `pubmed-etl/config/settings.py`（ETL 工具）
   - 修改 `LLM_PROVIDER` 读取逻辑（新增 `ETL_LLM_PROVIDER` 优先）
   - 更新注释说明环境变量命名

3. `.env.example`（仓库根）
   - 新增两处注释示例，明确各自生效范围：
     ```
     # ===== 主项目（multiomics-agent）LLM 供应商 =====
     # AGENT_LLM_PROVIDER=zhipu     （可选，默认 deepseek）

     # ===== pubmed-etl LLM 供应商 =====
     # ETL_LLM_PROVIDER=deepseek
     ```
   - API 密钥块保持通用（原有注释不变）

### 测试与验证

**主项目**：
- 新增/更新 `tests/unit/test_config.py`（mock 环境变量验证）
  - `AGENT_LLM_PROVIDER=zhipu` → `LLM_PROVIDER == 'zhipu'`
  - 仅 `LLM_PROVIDER=zhipu` → 向后兼容
  - 两者都无 → 默认 `deepseek`
  - `AGENT_LLM_PROVIDER` 优先于兼容别名

**pubmed-etl**：
- 为 `pubmed-etl/config/settings.py` 做同样验证（通过命令行或独立单元测试）

**回归测试**：
- 主项目：`python -m pytest tests/unit/ -q`
- pubmed-etl：`python -m pytest pubmed-etl/tests/ -q`
- smoke：`python -c "from src.config import LLM_PROVIDER; print(LLM_PROVIDER)"` 输出正确

## 实际效果示例

### 场景 1：主项目用智谱，ETL 用 DeepSeek

```bash
# .env
AGENT_LLM_PROVIDER=zhipu
ETL_LLM_PROVIDER=deepseek

# 主项目输出
python -c "from src.config import LLM_PROVIDER; print(LLM_PROVIDER)"
# → zhipu

# ETL 输出
cd pubmed-etl && python -c "from config.settings import LLM_PROVIDER; print(LLM_PROVIDER)"
# → deepseek
```

### 场景 2：两项目都用 DeepSeek（向后兼容）

```bash
# .env（仅设置兼容别名）
LLM_PROVIDER=deepseek

# 主项目输出（zhipu 未设置，fallback 到 LLM_PROVIDER）
# → deepseek
```

## 与现有机制的关系

- **不影响 `LLM_PROVIDER_CONFIGS` 字典结构**（deepseek/openai/zhipu 配置保持不变）
- **不影响计划 2 的 `build_llm_func(provider)`**（主项目继续用 `AGENT_LLM_PROVIDER`，ETL 用 `ETL_LLM_PROVIDER`）
- **不影响 LightRAG 或 pubmed-etl 的现有测试**（通过 mock 环境变量可覆盖）

## 后续可扩展

若两项目将来需要「同一 provider 不同账号」，可在各自的 `_PROVIDER_CONFIGS` 中为不同账号定义不同 key 映射（如 `ETL_ZHIPU_API_KEY`），但当前场景无需此扩展。
