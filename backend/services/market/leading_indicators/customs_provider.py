"""L2-海关进出口数据 Provider — HS编码细分进出口数据。

领先性：中领先（1-3 月）
数据源：akshare ak.china_imports_export（海关总署进出口数据）
逻辑：
  1. 拉取最新月度进出口数据
  2. HS编码细分映射到板块（如8542集成电路→半导体）
  3. 计算同比/环比，>10%为positive，<-10%为negative
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# HS编码 → 板块映射（4位HS编码前缀）
_HS_TO_SECTOR = {
    "8542": "半导体",    # 集成电路
    "8541": "半导体",    # 二极管/晶体管
    "8517": "科技",      # 电话/通信设备
    "8471": "科技",      # 自动数据处理设备
    "8703": "汽车",      # 汽车
    "8708": "汽车",      # 汽车零部件
    "2710": "化工",      # 石油产品
    "2818": "化工",      # 化工品
    "7403": "有色",      # 铜及其制品
    "7601": "有色",      # 铝及其制品
    "7208": "有色",      # 钢铁产品
    "3004": "医药",      # 药品
    "9018": "医药",      # 医疗器械
    "8537": "新能源",    # 电力控制设备
    "8501": "新能源",    # 电机
    "8507": "新能源",    # 蓄电池/锂电池
}


class CustomsProvider:
    """海关进出口数据 Provider。"""

    @property
    def provider_name(self) -> str:
        return "customs"

    @property
    def leading_level(self) -> str:
        return "medium"

    def fetch(self, lookback_days: int = 7) -> list:
        """拉取海关进出口数据。"""
        from services.market.leading_indicators.base import LeadingSignal
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout

        try:
            import akshare as ak
        except ImportError:
            logger.warning("[customs] akshare 未安装")
            return []

        # ak.china_imports_export 拉取最新进出口数据
        try:
            df = call_akshare_with_timeout(
                ak.china_imports_export,
                timeout=20,
            )
        except Exception as e:
            logger.warning(f"[customs] china_imports_export 调用失败: {e}")
            return []

        if df is None or df.empty:
            logger.info("[customs] 无进出口数据")
            return []

        df.columns = [str(c).strip() for c in df.columns]
        signals = []

        # 遍历 HS 编码行，按板块聚合
        sector_stats = {}
        for _, row in df.iterrows():
            try:
                hs_code = str(row.get("商品编码", "") or row.get("HS编码", "") or row.get("编码", "") or "")
                hs_prefix = hs_code[:4] if hs_code else ""
                sector = _HS_TO_SECTOR.get(hs_prefix, "")
                if not sector:
                    continue

                # 金额/数量/同比
                amount = float(row.get("人民币金额", 0) or row.get("金额", 0) or 0)
                yoy_str = str(row.get("同比", "") or row.get("人民币金额同比", "") or "")
                yoy = self._parse_pct(yoy_str)

                if sector not in sector_stats:
                    sector_stats[sector] = {"total_amount": 0, "yoy_list": [], "hs_codes": set()}
                sector_stats[sector]["total_amount"] += amount
                if yoy is not None:
                    sector_stats[sector]["yoy_list"].append(yoy)
                sector_stats[sector]["hs_codes"].add(hs_prefix)
            except Exception as e:
                logger.debug(f"[customs] 解析行失败: {e}")
                continue

        # 生成信号
        today = datetime.now().strftime("%Y-%m-%d")
        for sector, stats in sector_stats.items():
            avg_yoy = sum(stats["yoy_list"]) / len(stats["yoy_list"]) if stats["yoy_list"] else 0

            if avg_yoy > 10:
                direction = "positive"
            elif avg_yoy < -10:
                direction = "negative"
            else:
                direction = "neutral"

            signals.append(LeadingSignal(
                signal_type="customs_data",
                leading_level=self.leading_level,
                title=f"{sector}板块进出口同比{avg_yoy:+.1f}%",
                summary=f"{sector}板块近月进出口总额{stats['total_amount']:.1f}万元，同比{avg_yoy:+.1f}%，涉及HS编码: {', '.join(stats['hs_codes'])}",
                source_url="http://www.customs.gov.cn/",
                publish_date=today,
                affected_sectors=[sector],
                affected_themes=[],
                direction=direction,
                confidence=0.6,
                raw_data={
                    "sector": sector,
                    "total_amount": stats["total_amount"],
                    "avg_yoy": avg_yoy,
                    "hs_codes": list(stats["hs_codes"]),
                },
                metric_value=avg_yoy,
                metric_unit="%",
                metric_yoy=avg_yoy,
            ))

        # 按同比绝对值降序，最多取 10 条
        signals.sort(key=lambda s: abs(s.metric_yoy or 0), reverse=True)
        signals = signals[:10]
        logger.info(f"[customs] 抓取 {len(signals)} 条海关进出口信号")
        return signals

    def _parse_pct(self, pct_str: str):
        """解析百分比字符串。"""
        import re
        if not pct_str:
            return None
        match = re.search(r"(-?\d+(?:\.\d+)?)", str(pct_str).replace("%", ""))
        if match:
            return float(match.group(1))
        return None

    def map_to_sectors(self, text: str) -> list:
        """复用基类板块映射。"""
        from services.market.leading_indicators.base import LeadingIndicatorProvider
        return LeadingIndicatorProvider.map_to_sectors(self, text)
