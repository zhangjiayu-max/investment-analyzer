"""L1-产业资本增减持 Provider — 上市公司高管/大股东增减持动向。

领先性：强领先（3-12 月）
数据源：akshare ak.stock_hold_management_detail（高管增减持明细）
逻辑：
  1. 拉取近 N 天高管增减持明细
  2. 按板块聚合统计净增持/净减持
  3. 净增持行业 → positive 信号
  4. 净减持行业 → negative 信号
"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class InsiderTradingProvider:
    """产业资本增减持 Provider。"""

    @property
    def provider_name(self) -> str:
        return "insider_trading"

    @property
    def leading_level(self) -> str:
        return "strong"

    def fetch(self, lookback_days: int = 7) -> list:
        """拉取近 N 天产业资本增减持信号。"""
        from services.market.leading_indicators.base import LeadingSignal
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout

        try:
            import akshare as ak
        except ImportError:
            logger.warning("[insider_trading] akshare 未安装")
            return []

        # ak.stock_hold_management_detail 按日期拉取高管增减持
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)

        # 逐天拉取（接口按天查询），最多拉 lookback_days 天
        all_records = []
        for i in range(lookback_days):
            check_date = start_date + timedelta(days=i)
            date_str = check_date.strftime("%Y%m%d")
            try:
                df = call_akshare_with_timeout(
                    ak.stock_hold_management_detail,
                    symbol="全部",
                    start_date=date_str,
                    end_date=date_str,
                    timeout=10,
                )
                if df is not None and not df.empty:
                    all_records.append(df)
            except Exception:
                continue
            # 限制总拉取量，避免过多请求
            total_rows = sum(len(d) for d in all_records)
            if total_rows > 500:
                break

        if not all_records:
            logger.info("[insider_trading] 无增减持数据")
            return []

        import pandas as pd
        df_all = pd.concat(all_records, ignore_index=True) if len(all_records) > 1 else all_records[0]
        df_all.columns = [str(c).strip() for c in df_all.columns]

        # 按板块聚合净增持/减持金额
        sector_stats = defaultdict(lambda: {"buy_amount": 0.0, "sell_amount": 0.0, "count": 0, "stocks": set()})

        for _, row in df_all.iterrows():
            try:
                # 变动方向/金额
                change_type = str(row.get("变动方向", "") or row.get("变动类型", "") or "")
                # 增持 / 减持
                amount_str = str(row.get("变动金额", "") or row.get("交易金额", "") or "0")
                amount = self._parse_amount(amount_str)

                stock_name = str(row.get("变动人", "") or row.get("高管姓名", "") or "")
                stock_code = str(row.get("股票代码", "") or row.get("代码", "") or "")
                company = str(row.get("变动人", "") or "")

                # 板块映射（基于公司名/股票名）
                # 注意：接口返回的可能是高管名而非公司名，需要从 stock_code 反查
                # 简化处理：用公司名做板块映射
                text_for_mapping = f"{company} {stock_name}"
                sectors = self.map_to_sectors(text_for_mapping)

                for sector in sectors:
                    if "增持" in change_type:
                        sector_stats[sector]["buy_amount"] += amount
                    elif "减持" in change_type:
                        sector_stats[sector]["sell_amount"] += amount
                    sector_stats[sector]["count"] += 1
                    sector_stats[sector]["stocks"].add(stock_code)
            except Exception as e:
                logger.debug(f"[insider_trading] 解析行失败: {e}")
                continue

        # 生成信号：每个板块一条信号
        signals = []
        for sector, stats in sector_stats.items():
            net_amount = stats["buy_amount"] - stats["sell_amount"]
            if stats["count"] < 3:  # 少于3笔不生成信号
                continue

            if net_amount > 0:
                direction = "positive"
                title = f"{sector}板块产业资本净增持 {net_amount:.1f}万元"
            elif net_amount < 0:
                direction = "negative"
                title = f"{sector}板块产业资本净减持 {abs(net_amount):.1f}万元"
            else:
                continue  # 净变动为0不生成信号

            signals.append(LeadingSignal(
                signal_type="insider_trading",
                leading_level=self.leading_level,
                title=title,
                summary=f"近{lookback_days}天{sector}板块高管增减持统计：增持{stats['buy_amount']:.1f}万，减持{stats['sell_amount']:.1f}万，净{'增持' if net_amount>0 else '减持'}{abs(net_amount):.1f}万，涉及{len(stats['stocks'])}只股票",
                source_url="https://data.eastmoney.com/executive/",
                publish_date=end_date.strftime("%Y-%m-%d"),
                affected_sectors=[sector],
                affected_themes=[],
                direction=direction,
                confidence=0.6,
                raw_data={
                    "sector": sector,
                    "buy_amount": stats["buy_amount"],
                    "sell_amount": stats["sell_amount"],
                    "net_amount": net_amount,
                    "stock_count": len(stats["stocks"]),
                    "transaction_count": stats["count"],
                },
                metric_value=net_amount,
                metric_unit="万元",
            ))

        # 按净金额绝对值降序，最多取 15 条
        signals.sort(key=lambda s: abs(s.metric_value or 0), reverse=True)
        signals = signals[:15]
        logger.info(f"[insider_trading] 抓取 {len(signals)} 条产业资本增减持信号")
        return signals

    def _parse_amount(self, amount_str: str) -> float:
        """解析金额字符串为万元。"""
        import re
        if not amount_str:
            return 0.0
        # 去除非数字字符
        match = re.search(r"(\d+(?:\.\d+)?)", amount_str.replace(",", ""))
        if not match:
            return 0.0
        value = float(match.group(1))
        # 单位转换
        if "亿" in amount_str:
            value *= 10000  # 亿→万
        return value

    def map_to_sectors(self, text: str) -> list:
        """复用基类板块映射。"""
        from services.market.leading_indicators.base import LeadingIndicatorProvider
        return LeadingIndicatorProvider.map_to_sectors(self, text)
