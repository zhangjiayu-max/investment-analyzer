# 图表交互标准化设计稿

> 日期：2026-07-16
> 目标：统一图表交互体验，支持点击数据点查看详情 + 买卖点标记

## 一、现状

| 图表 | 位置 | 技术 | 交互 | 买卖点 |
|------|------|------|------|--------|
| 5年走势图 | PortfolioManagement.vue 弹窗 | 自定义 SVG | hover 提示，brush 缩放 | 无 |
| 净值走势图 | PortfolioManagement.vue 交易Tab | LineChart (ECharts) | axis tooltip | 无 |
| K线图 | TaskDetail.vue | StockChart (ECharts) | cross tooltip | 无 |

后端 `get_fund_nav_history()` 已返回 `transactions` 数组（含买卖日期、价格、份额），但前端未使用。

## 二、方案

### 2.1 5年走势图：SVG → ECharts

**删除**：SVG 模板 + 所有 chart5y* 计算属性/handler（~130行 SVG + ~200行 JS）
**新增**：ECharts 实例，配置项：

- 净值折线 + 面积填充（保持现有视觉风格）
- **tooltip**：`trigger: 'axis'` + `triggerOn: 'click'`（点击锁定，再点关闭）
- **dataZoom**：slider + inside，替代现有 brush 缩放
- **买卖点标记**：`markPoint` 或 scatter 系列
  - 红色 ▲ 朝上 = 买入点
  - 绿色 ▼ 朝下 = 卖出点
  - hover 显示：日期、价格、份额、金额
- 统计卡片保持不变（区间、累计涨跌幅、年化、最大回撤等）

### 2.2 净值走势图：增加买卖点

在 `LineChart.vue` 组件中增加 `markPoints` prop，支持传入买卖点数据：

```js
markPoints: [
  { type: 'buy', date: '2026-01-15', price: 1.2345, shares: 5000 },
  { type: 'sell', date: '2026-03-20', price: 1.3456, shares: 2000 },
]
```

在 ECharts series 中通过 `markPoint` 渲染：

```js
markPoint: {
  data: markPoints.map(p => ({
    name: p.type === 'buy' ? '买入' : '卖出',
    coord: [p.date, p.price],
    value: `¥${p.amount}`,
    symbol: p.type === 'buy' ? 'triangle' : 'triangle',
    symbolRotate: p.type === 'buy' ? 0 : 180,
    itemStyle: { color: p.type === 'buy' ? '#ef4444' : '#22c55e' },
  })),
}
```

### 2.3 StockChart K线图：增加买卖点

StockChart 用于 TaskDetail 的基金 K 线展示。当前无交易数据传入，需要：

1. 增加 `transactions` prop
2. 在 K 线系列上叠加 `markPoint` 标记买卖点

## 三、改动文件清单

| 文件 | 改动 |
|------|------|
| `frontend/src/components/portfolio/PortfolioManagement.vue` | 1) SVG 图表替换为 ECharts；2) 删除 chart5y* 计算属性；3) 净值走势图传入 transactions |
| `frontend/src/components/charts/LineChart.vue` | 增加 `markPoints` prop，渲染买卖点标记 |
| `frontend/src/components/valuation/StockChart.vue` | 增加 `transactions` prop，渲染买卖点标记 |
| `frontend/src/composables/useChartTheme.js` | 可选：增加买卖点配色常量 |

## 四、影响范围

- 5年走势图：交互从 hover 改为 click 锁定 + hover 辅助，缩放从 brush 改为 dataZoom slider
- 净值走势图：同系列多条线时，买卖点只标记在第一条线上
- StockChart：K 线图已有 MA 均线系列，买卖点标记在 K 线系列上

## 五、风险

1. **ECharts 动态导入**：5年走势图在弹窗中，首次打开需加载 echarts 模块（~1MB），与 StockChart 复用已加载的模块
2. **tooltip 性能**：数据点可达 1800+（5年），`trigger: 'axis'` 性能无问题
3. **向后兼容**：LineChart 的 markPoints prop 可选，不影响现有调用