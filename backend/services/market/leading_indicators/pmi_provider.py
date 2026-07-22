"""L2-PMI 分项 Provider — 制造业 PMI 分项数据。

领先性：中领先（1-3 月）
数据源：akshare ak.macro_china_pmi（国家统计局 PMI 数据）
逻辑：
  1. 拉取最新 PMI 数据及分项（新订单/新出口订单/价格）
  2. >55 为强扩张 positive，<50 为收缩 negative
  3. 价格分项 >60 为通胀预警
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# PMI 分项 → 重要性
_PMI_SUBITEMS = {
    "新订单": "new_order",
    "新出口订单": "new_export_order",
    "生产": "production",
    "原材料购进价格": "input_price",
    "出厂价格": "output_price",
    "从业人员": "employment",
    "生产经营活动预期": "expectation",
}


class PmiProvider:
    """PMI 分项 Provider。"""

    @property
    def provider_name(self) -> str:
        return "pmi"

    @property
    def leading_level(self) -> str:
        return "medium"

    def fetch(self, lookback_days: int = 7) -> list:
        """拉取 PMI 分项数据。"""
        from services.market.leading_indicators.base import LeadingSignal
        from services.market.leading_indicators.akshare_utils import call_akshare_with_timeout

        try:
            import akshare as ak
        except ImportError:
            logger.warning("[pmi] akshare 未安装")
            return []

        # ak.macro_china_pmi 拉取制造业 PMI
        try:
            df = call_akshare_with_timeout(
                ak.macro_china_pmi,
                timeout=20,
            )
        except Exception as e:
            # 接口名可能变更，尝试备选
            try:
                df = call_akshare_with_timeout(
                    ak.macro_china_pmi_yearly,
                    timeout=20,
                )
            except Exception as e2:
                logger.warning(f"[pmi] macro_china_pmi 调用失败: {e} / {e2}")
                return []

        if df is None or df.empty:
            logger.info("[pmi] 无 PMI 数据")
            return []

        df.columns = [str(c).strip() for c in df.columns]

        # 取最新月份数据
        latest = df.iloc[-1] if len(df) > 0 else None
        if latest is None:
            return []

        signals = []
        today = datetime.now().strftime("%Y-%m-%d")
        report_date = str(latest.get("月份", "") or latest.get("报告日期", "") or today)

        # 整体 PMI
        total_pmi = self._safe_float(latest.get("制造业PMI", "") or latest.get("PMI", "") or latest.get("综合PMI", ""))
        if total_pmi is not None:
            if total_pmi >= 55:
                direction = "positive"
            elif total_pmi < 50:
                direction = "negative"
            else:
                direction = "neutral"

            signals.append(LeadingSignal(
                signal_type="pmi_subitem",
                leading_level=self.leading_level,
                title=f"制造业PMI={total_pmi:.1f}（{report_date}）",
                summary=f"制造业PMI {total_pmi:.1f}，{('强扩张' if total_pmi>=55 else '扩张' if total_pmi>=50 else '收缩')}。>50为扩张，>55为强扩张，<50为收缩。",
                source_url="http://www.stats.gov.cn/",
                publish_date=today,
                affected_sectors=["制造业"],  # PMI 影响全板块
                affected_themes=["制造业"],
                direction=direction,
                confidence=0.7,
                raw_data={"pmi": total_pmi, "date": report_date},
                metric_value=total_pmi,
                metric_unit="",
            ))

        # 分项 PMI
        for cn_name, en_key in _PMI_SUBITEMS.items():
            value = self._safe_float(latest.get(cn_name, "") or latest.get(en_key, ""))
            if value is None:
                continue

            if value >= 55:
                direction = "positive"
            elif value < 50:
                direction = "negative"
            else:
                direction = "neutral"

            # 价格分项 >60 为通胀预警
            if "价格" in cn_name and value >= 60:
                direction = "negative"  # 原材料价格上涨对制造业不利

            signals.append(LeadingSignal(
                signal_type="pmi_subitem",
                leading_level=self.leading_level,
                title=f"PMI分项-{cn_name}={value:.1f}（{report_date}）",
                summary=f"PMI分项 {cn_name} {value:.1f}，{('扩张' if value>=50 else '收缩')}。",
                source_url="http://www.stats.gov.cn/",
                publish_date=today,
                affected_sectors=["制造业"],
                affected_themes=[cn_name],
                direction=direction,
                confidence=0.6,
                raw_data={"subitem": cn_name, "value": value, "date": report_date},
                metric_value=value,
                metric_unit="",
            ))

        # 限制最多 8 条
        signals = signals[:8]
        logger.info(f"[pmi] 抓取 {len(signals)} 条 PMI 分项信号")
        return signals

    def _safe_float(self, value):
        """安全转 float。"""
        if value is None or value == "":
            return None
        try:
            return float(str(value).replace("%", "").strip())
        except (ValueError, TypeError):
            return None

    def map_to_sectors(self, text: str) -> list:
        """复用基类板块映射。"""
        from services.market.leading_indicators.base import LeadingIndicatorProvider
        return LeadingIndicatorProvider.map_to_sectors(self, text)
