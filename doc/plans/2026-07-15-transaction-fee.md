# 交易手续费功能设计稿

> 日期：2026-07-15
> 场景：持仓列表加仓/赎回时，实际买入金额 = 投入金额 - 手续费；实际赎回到账 = 份额×净值 - 手续费

## 一、现状分析

### 已有能力（无需改动）
| 位置 | 状态 |
|------|------|
| `confirm_transaction()` db/portfolio.py:984 | ✅ 已支持 `fee` 参数，买入/卖出/转换扣费逻辑正确 |
| `ConfirmTransactionRequest` models/portfolio.py:48 | ✅ 已有 `fee: float = 0` 字段 |
| `confirm_transaction_api()` routers/portfolio.py:927 | ✅ 已传 `req.fee` |
| 前端手动确认弹窗 PortfolioManagement.vue:5889 | ✅ 已有 fee 输入框 + 实时预览扣费后份额 |
| 前端 `submitConfirmTx()` :3342 | ✅ payload 含 `fee: confirmTxFee.value \|\| 0` |
| `portfolio_transactions` 表 | ✅ 已有 `fee` 列 |

### 缺失能力（本次需补齐）
| 位置 | 问题 |
|------|------|
| `auto_confirm_transaction_api()` routers/portfolio.py:969 | 单笔自动确认未传 fee → fee=0 |
| `auto_confirm_due_transactions()` db/portfolio.py:1298 | 批量自动确认未传 fee → fee=0 |
| 前端 `fetchAutoNav()` :3308 | "自动获取净值"按钮不传 fee |
| 前端 `batchAutoConfirm()` :3325 | 批量确认不传 fee |
| 买入/卖出表单 | 提交 pending 交易时无 fee 字段，用户只能在 T+1 手动填 |
| 后台定时自动确认 app.py | 调用 `auto_confirm_due_transactions` 也不含 fee |

### 核心矛盾
手动确认路径可用但繁琐（用户每次需自己算手续费填入）；自动确认路径完全跳过手续费 → 持仓成本不准。

## 二、设计方案

### 方案对比

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A. 纯手动 | 买入表单加 fee 输入框，pending 时存 fee，确认时用 | 简单、精确 | 用户需每次查费率手填，易遗漏 |
| B. 自动按费率 | 配置全局费率，自动确认时按费率算 fee | 自动化、无感 | 赎回费按持有期阶梯计费难精确 |
| **C. 混合（推荐）** | 费率配置自动算 + 用户可覆盖 | 自动化 + 可纠偏 | 实现稍复杂 |

### 推荐：方案 C — 费率配置自动计算 + 手动可覆盖

### 2.1 费率配置（db/config.py）

新增配置项（默认值偏保守，用户可在设置页调整）：

| key | 默认值 | 说明 |
|-----|--------|------|
| `fee.buy_rate` | 0.0015 | 申购费率 0.15%（多数平台打折后） |
| `fee.sell_rate_lt7d` | 0.015 | 持有<7天赎回费 1.5%（惩罚性） |
| `fee.sell_rate_lt1y` | 0.005 | 持有 7天-1年 0.5% |
| `fee.sell_rate_lt2y` | 0.0025 | 持有 1-2年 0.25% |
| `fee.sell_rate_ge2y` | 0.0 | 持有≥2年 0% |
| `fee.convert_rate` | 0.0 | 转换费（多数平台免费） |
| `fee.auto_calc_enabled` | true | 自动确认时是否按费率自动计算 |

**开关控制**：`fee.auto_calc_enabled` 默认 true（符合"新开关默认 false"约束的例外：这不是 LLM 调用开关，而是业务计算开关，且用户明确要求加手续费）。

### 2.2 赎回费持有期判定

赎回费按持有期阶梯，需判定卖出份额的买入日期：
- 简化方案：按持仓的 `buy_date`（加权平均成本日）判定 → 单一持有期
- 不做 FIFO 多批次精确判定（复杂度过高，收益有限）
- 若 `buy_date` 缺失，按最高费率（lt7d）兜底

### 2.3 后端改动

#### 2.3.1 新增工具函数 `services/fee_calculator.py`

```python
def calc_buy_fee(amount: float, user_id: str = "default") -> float:
    """申购费 = 买入金额 × 申购费率"""
    rate = get_config_float('fee.buy_rate', 0.0015)
    return round(amount * rate, 2)

def calc_sell_fee(shares: float, nav: float, holding: dict, user_id: str = "default") -> float:
    """赎回费 = 卖出金额 × 持有期对应费率"""
    gross = shares * nav
    buy_date = holding.get('buy_date')  # YYYY-MM-DD
    holding_days = _days_between(buy_date, today)
    if holding_days < 7:
        rate = get_config_float('fee.sell_rate_lt7d', 0.015)
    elif holding_days < 365:
        rate = get_config_float('fee.sell_rate_lt1y', 0.005)
    elif holding_days < 730:
        rate = get_config_float('fee.sell_rate_lt2y', 0.0025)
    else:
        rate = get_config_float('fee.sell_rate_ge2y', 0.0)
    return round(gross * rate, 2)

def calc_convert_fee(shares: float, nav: float, user_id: str = "default") -> float:
    """转换费 = 转出金额 × 转换费率"""
    rate = get_config_float('fee.convert_rate', 0.0)
    return round(shares * nav * rate, 2)
```

#### 2.3.2 修改 `auto_confirm_transaction_api()` routers/portfolio.py:940

```python
# 自动确认时按费率计算 fee
from services.fee_calculator import calc_buy_fee, calc_sell_fee, calc_convert_fee
from db.config import get_config

fee = 0
if get_config('fee.auto_calc_enabled', 'true') == 'true':
    if tx_type == 'buy':
        sub_amount = tx.get('submitted_amount') or tx.get('amount') or 0
        fee = calc_buy_fee(sub_amount)
    elif tx_type == 'sell':
        holding = get_holding(tx['holding_id']) if tx.get('holding_id') else None
        sub_shares = tx.get('submitted_shares') or tx.get('shares') or 0
        fee = calc_sell_fee(sub_shares, confirmed_price, holding or {})
    elif tx_type == 'convert':
        sub_shares = tx.get('submitted_shares') or tx.get('shares') or 0
        fee = calc_convert_fee(sub_shares, confirmed_price)

ok = confirm_transaction(tx_id, confirmed_price, fee=fee)
```

#### 2.3.3 修改 `auto_confirm_due_transactions()` db/portfolio.py:1258

批量自动确认同样按费率计算 fee（调用同样的 `services.fee_calculator`）。

#### 2.3.4 修改 `confirm_transaction_api()` routers/portfolio.py:927

手动确认时，若用户未填 fee（fee=0）且开关开启，自动计算并回填：
```python
fee = req.fee
if fee == 0 and get_config('fee.auto_calc_enabled', 'true') == 'true':
    # 按 tx_type 自动算
    fee = _auto_calc_fee(tx, req.confirmed_price, ...)
ok = confirm_transaction(tx_id, req.confirmed_price, ..., fee=fee)
```

这样手动确认弹窗里用户不填也会自动算，填了则以用户值为准（可纠偏）。

### 2.4 前端改动

#### 2.4.1 手动确认弹窗（PortfolioManagement.vue:5889）

- fee 输入框旁加"自动计算"按钮，点击调 `/api/portfolio/transactions/{id}/calc-fee` 获取预估 fee
- 或：打开弹窗时自动调一次预填 fee（用户可改）

#### 2.4.2 买入/卖出表单

- 买入表单加"预估手续费"只读展示（金额 × 申购费率），提交时仍 pending，fee 在确认时算
- 卖出表单同理

#### 2.4.3 交易记录列表

- 交易记录表加显示 `fee` 列（已有数据，只需展示）

### 2.5 新增 API

```
POST /api/portfolio/transactions/{tx_id}/calc-fee
  → { "fee": 12.50, "rate": 0.0015, "basis": "申购费 0.15%" }
```
用于前端弹窗预填 fee。

## 三、改动文件清单

| 文件 | 改动 |
|------|------|
| `backend/services/fee_calculator.py` | **新建** — 费率计算工具 |
| `backend/db/config.py` | 新增 7 个 fee.* 配置项 |
| `backend/routers/portfolio/portfolio.py` | 修改 `auto_confirm_transaction_api`、`confirm_transaction_api`；新增 `calc-fee` API |
| `backend/db/portfolio.py` | 修改 `auto_confirm_due_transactions` 传 fee |
| `frontend/src/components/portfolio/PortfolioManagement.vue` | 确认弹窗预填 fee、交易列表展示 fee |
| `frontend/src/api/index.js` | 新增 `calcTransactionFee` |

## 四、风险与约束

1. **赎回费持有期判定不精确**：用 `buy_date` 单一日期，非 FIFO。若用户多次加仓，持有期偏短 → 赎回费偏高。可接受（保守估算）。
2. **费率因基金而异**：货币基金 0 申购费、C 类基金 0 申购费。本次用全局默认费率，不做按基金类型区分（后续可扩展 per-fund 费率覆盖）。
3. **向后兼容**：历史已确认交易 fee=0 不回溯；仅新确认的交易按新逻辑。
4. **开关默认值**：`fee.auto_calc_enabled` 默认 true（用户明确要求加手续费，非 LLM 开关）。

## 五、待确认

1. 赎回费是否需要精确到 FIFO 多批次？建议否（复杂度高）。
2. 是否需要 per-fund 费率覆盖（某些基金 0 申购费）？建议二期。
3. 买入表单是否需要展示预估手续费？建议是（提升透明度）。
