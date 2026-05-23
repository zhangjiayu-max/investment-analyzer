# 投资分析助手 — 设计文档

## 需求

一个 Jupyter Notebook 工具，用于：
1. 读取微信公众号文章链接，提取文字和图片
2. 获取股票/基金的行情和估值数据
3. 结合用户提供的指数估值数据，给出投资建议
4. 支持图片显示（公众号文章中的估值图表等）

## 技术选型

| 能力 | 方案 | 理由 |
|------|------|------|
| 公众号抓取 | requests + BeautifulSoup | 轻量，公众号文章可直接 HTTP 访问 |
| 行情数据 | akshare | 免费、无需注册、覆盖 A 股 + 基金 |
| 图片处理 | Pillow | 标准库级别，够用 |
| 可视化 | plotly | 交互式图表，Jupyter 原生支持 |
| 技术指标 | ta-lib | 项目已有，40+ 指标 |

## 模块设计

### article_reader.py — 公众号文章读取

```python
fetch_article(url) → {
    "title": str,
    "author": str,
    "publish_time": str,
    "content_text": str,    # 纯文本
    "images": list[str],    # 图片 URL 列表
}
download_images(urls, save_dir) → list[str]  # 返回本地路径
```

### market_data.py — 行情数据

```python
get_stock_history(symbol, days) → DataFrame    # 股票历史行情
get_fund_nav(fund_code) → DataFrame            # 基金净值
get_index_valuation(index_code) → dict         # 指数估值
get_stock_info(symbol) → dict                  # 个股基本面
```

### valuation.py — 估值分析

```python
analyze_stock(symbol, user_valuation) → dict   # 综合分析
analyze_fund(fund_code, user_valuation) → dict  # 基金分析
generate_report(results) → str                 # 生成报告
```

估值逻辑：
- PE/PB 百分位 < 30% → 低估
- 30%-70% → 合理
- > 70% → 高估
- 结合用户提供的指数估值做相对比较

## 使用流程

```
1. 粘贴公众号链接 → 抓取文章 + 图片
2. 输入指数估值数据（手动或截图）
3. 输入股票/基金代码 → 拉取数据
4. 运行分析 → 输出建议 + 图表
5. 生成报告
```
