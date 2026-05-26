---
name: valuation-trends
description: 查询指数估值历史数据，生成 PE/PB 分位点趋势图。当用户询问指数估值、想看走势、问"有哪些指数"、或提到"估值"、"分位点"、"PE"、"PB"时触发。
---

# 指数估值趋势

查询指数估值历史，生成趋势图，辅助投资判断。

## 触发条件

用户提到指数估值、分位点、PE、PB、走势、"贵不贵"、"能不能买"、"有哪些指数"时触发。

## 功能

1. **列出所有指数** — 查看库里有哪些指数、各有多少天数据
2. **查看某指数最新估值** — 最新一天的 PE/PB/分位点 + 所有可用日期
3. **查询历史** — 指定天数的估值历史
4. **生成趋势图** — PE-TTM + 分位点双面板走势图
5. **多指数对比** — 多个指数分位点叠加对比图

## 使用方式

### 列出所有指数

```bash
python3 <skill-dir>/scripts/query_valuation.py
```

### 模糊搜索（中文名或代码片段）

```bash
python3 <skill-dir>/scripts/query_valuation.py 红利
python3 <skill-dir>/scripts/query_valuation.py 500
```

输出示例：
```
共 3 个指数有估值数据:

  931468.CSI      红利质量     2026-04-01 ~ 2026-05-23  共 8 条
  000905.SH       中证500      2026-05-15 ~ 2026-05-23  共 3 条
  000300.SH       沪深300      2026-05-20 ~ 2026-05-20  共 1 条
```

### 查看某指数最新估值 + 可用日期

```bash
python3 <skill-dir>/scripts/query_valuation.py 931468.CSI
```

输出最新一天的完整指标，以及所有有数据的日期列表。

### 查询历史记录

```bash
python3 <skill-dir>/scripts/query_valuation.py 931468.CSI 30
```

### 生成趋势图

```bash
python3 <skill-dir>/scripts/plot_trends.py <指数代码> [天数] [输出文件]
```

### 多指数对比图

```bash
python3 <skill-dir>/scripts/plot_trends.py --compare <代码1> <代码2> ... [天数]
```

## 数据来源

数据库路径：`<项目根>/data/valuations.db`

数据由 `save-valuation` skill 写入，来源是其他 AI 工具对公众号图片的识别结果。

## 输出说明

### 趋势图包含

- **上图**：PE-TTM 折线 + 危险值/中位数/机会值参考线
- **下图**：PE 分位点面积图 + 低估区(<20%) / 高估区(>80%) 标线

### 估值判断参考

| 分位点区间 | 含义 |
|-----------|------|
| <20% | 低估区，历史便宜 |
| 20%-50% | 偏低，可关注 |
| 50%-80% | 偏高，谨慎 |
| >80% | 高估区，历史贵 |

## 依赖

- Python 3.10+
- `matplotlib`
