---
name: save-valuation
description: 保存 AI 识别的指数估值数据到数据库。当用户提供 AI 分析结果（JSON）或说"保存估值"、"入库"时触发。
---

# 估值数据保存

接收其他 AI 工具（如 Qclaw）识别出的指数估值数据，存入 SQLite 数据库。

## 触发条件

- 用户提供了 AI 识别的 JSON 数据，要求保存
- 用户说"保存估值"、"入库"、"save valuation"
- 用户提供了分析结果文件路径

## 功能

1. **接收 JSON 数据** — 支持文件、stdin、批量扫描
2. **自动提取日期** — 从 manifest.json 的文章标题中提取数据日期
3. **字段映射** — 自动将中英文指标名映射为标准 DB 字段
4. **更新 manifest** — 标记已分析的图片，避免重复分析

## 使用方式

### 从 JSON 文件保存

```bash
python3 <skill-dir>/scripts/save_valuation.py \
  --manifest /path/to/manifest.json \
  --data /path/to/ai_analysis.json
```

### 从 stdin 保存

```bash
echo '{"index_code":"931468.CSI","pe_ttm":13.96,...}' | \
  python3 <skill-dir>/scripts/save_valuation.py --manifest /path/to/manifest.json
```

### 指定日期

```bash
python3 <skill-dir>/scripts/save_valuation.py --data analysis.json --date 2026-05-23
```

## AI 输出格式

脚本支持多种 JSON 结构，会自动识别：

### 格式 1：扁平结构

```json
{
  "index_code": "931468.CSI",
  "index_name": "红利质量",
  "current_point": 35391.17,
  "pe_ttm": 13.96,
  "pe_percentile": 3.27
}
```

### 格式 2：metrics 子对象

```json
{
  "index_code": "931468.CSI",
  "index_name": "红利质量",
  "metrics": {
    "当前值": 13.96,
    "分位点": 3.27,
    "危险值": 21.83
  }
}
```

### 格式 3：中文 key

```json
{
  "指数代码": "931468.CSI",
  "指数名称": "红利质量",
  "市盈率TTM统计指标": {
    "当前值": 13.96,
    "分位点": 3.27
  }
}
```

### 格式 4：数组（批量）

```json
[
  {"index_code": "931468.CSI", "pe_ttm": 13.96, ...},
  {"index_code": "000905.SH", "pe_ttm": 12.34, ...}
]
```

## 日期优先级

1. `--date` 参数
2. JSON 数据中的 `snapshot_date` 或 `日期` 字段
3. manifest.json 中的 `article_date`（从标题自动提取）
4. 今天日期

## 数据库 schema

统一 `metric_type` 字段区分指标类型（市盈率 / 市净率 / 股息率 / 风险溢价等），所有指标共用相同的统计字段。

| 字段 | 说明 |
|------|------|
| index_code | 指数代码 |
| index_name | 指数名称 |
| snapshot_date | 数据日期 |
| current_point | 指数点位 |
| change_pct | 涨跌幅 |
| metric_type | 指标类型：市盈率 / 市净率 / 风险溢价 / 股息率 |
| current_value | 当前值 |
| percentile | 分位点 (%) |
| danger_value | 危险值 |
| median | 中位数 |
| opportunity_value | 机会值 |
| max_value | 历史最大值 |
| min_value | 历史最小值 |
| avg_value | 历史平均值 |
| zscore | Z 分数 |
| source_url | 来源文章链接 |
| source_image | 来源图片路径 |

## 与其他技能的关系

- **上游**：`download-wechat-images` 下载图片 → 其他 AI 工具分析图片 → 本技能保存结果
- **下游**：`valuation-trends` 读取本技能写入的数据库，提供查询和趋势图

## 依赖

- Python 3.10+（仅标准库，无第三方依赖）
