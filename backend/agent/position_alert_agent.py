"""加减仓预警Agent — 持仓变更时自动触发分析

触发条件：
1. 用户通过前端/API手动修改持仓（买入/卖出/增减仓）
2. 用户在对话中提到加减仓意图

输出：
- 风险评估（当前估值/趋势/集中度）
- 操作建议（是否合理、时机是否合适）
- 预警信号（🟢绿灯/🟡黄灯/🔴红灯/⚫黑灯）
- 复盘提醒（30/60天后自动提醒回顾）
"""

import json
import logging
import re
from datetime import datetime, timedelta

from db import (
    create_alert, list_alerts, get_latest_valuation,
    get_valuation_history, list_holdings,
)
from db.decisions import create_decision, match_pending_decisions
from llm_service import _call_llm

logger = logging.getLogger(__name__)

# ── 预警Agent系统提示词 ──────────────────────────────────────
POSITION_ALERT_PROMPT = """你是一位严谨的基金投资风控分析师，专注于加减仓决策的事前预警和事后复盘。

## 核心职责
当用户进行加仓/减仓/建仓/清仓操作时，你需要：
1. **即时风险评估**：基于当前估值、持仓集中度、市场趋势判断操作合理性
2. **给出预警信号**：如果操作存在风险，明确指出并给出信号等级
3. **提供建议**：如果操作合理，给出执行建议（分批/一次性/等待时机）

## 信号等级定义
- 🟢 **绿灯**：操作合理，估值和仓位都在安全区间
- 🟡 **黄灯**：操作有一定风险，建议关注或分批执行
- 🔴 **红灯**：操作风险较高，建议暂缓或调整策略
- ⚫ **黑灯**：严重风险信号，强烈建议不要执行

## 分析维度（必须全部覆盖）

### 1. 估值维度
- 当前PE/PB百分位：<20%为低估，20-50%为合理偏低，50-80%为合理偏高，>80%为高估
- Z-score：<-1为深度低估，-1~0为低估，0~1为高估，>1为深度高估
- 估值趋势：近60天估值是在上升还是下降

### 2. 仓位维度
- 单只基金占总仓位比例：>30%为过度集中，>50%为严重集中
- 同类基金合计占比：同行业/主题基金合计>40%为集中度过高
- 总仓位水平：满仓时减仓合理，空仓时加仓合理

### 3. 历史维度
- 该基金近期是否频繁操作（30天内>3次为频繁）
- 上次同方向操作的结果如何（盈利/亏损）
- 是否存在"追涨杀跌"模式

## 输出格式
```
## 📊 加减仓预警分析

### 操作概要
- 基金：[名称]（[代码]）
- 操作：[加仓/减仓/建仓/清仓]
- 金额/比例：[具体数值]

### 🚦 预警信号：[🟢绿灯/🟡黄灯/🔴红灯/⚫黑灯]

### 详细分析
1. **估值分析**：...
2. **仓位分析**：...
3. **历史分析**：...

### 💡 操作建议
- [具体建议]

### ⚠️ 风险提示
- [需要关注的风险]
```

## 负面约束
- 不要给出具体的买入/卖出时点预测
- 不要编造数据，所有数据必须来自工具查询
- 不要过度乐观或悲观，保持客观中立
- 不要忽略任何分析维度，即使某个维度数据缺失也要说明
"""


class PositionAlertAgent:
    """加减仓预警Agent"""

    def __init__(self):
        self.prompt = POSITION_ALERT_PROMPT

    def analyze_position_change(
        self,
        user_id: str,
        fund_code: str,
        fund_name: str,
        action: str,  # add / reduce / buy / sell
        amount: float = 0,
        shares: float = 0,
    ) -> dict:
        """
        分析持仓变更，返回预警结果。

        Returns:
            {
                "signal": "green|yellow|red|black",
                "signal_emoji": "🟢|🟡|🔴|⚫",
                "analysis": "完整分析文本",
                "alert_id": 123,  # 创建的预警ID
                "decision_id": 123,  # 创建的决策档案ID（黄灯以上）
                "suggestions": ["建议1", "建议2"],
                "risks": ["风险1", "风险2"],
            }
        """
        # 1. 收集数据
        context = self._gather_context(user_id, fund_code, fund_name, action)

        # 2. 构建分析请求
        user_message = self._build_analysis_request(
            fund_code, fund_name, action, amount, shares, context
        )

        # 3. 调用LLM分析
        analysis_result = self._call_analysis_llm(user_message)

        # 4. 解析预警信号
        signal = self._extract_signal(analysis_result)

        # 5. 创建项目内预警通知（所有信号等级都创建）
        alert_id = self._create_alert_notification(
            fund_code, fund_name, action, signal, analysis_result
        )

        # 6. 黄灯及以上自动创建决策档案
        decision_id = None
        if signal in ("yellow", "red", "black"):
            decision_id = self._create_alert_decision(
                fund_code, fund_name, action, signal, analysis_result
            )

        return {
            "signal": signal,
            "signal_emoji": {"green": "🟢", "yellow": "🟡", "red": "🔴", "black": "⚫"}[signal],
            "analysis": analysis_result,
            "alert_id": alert_id,
            "decision_id": decision_id,
            "suggestions": self._extract_suggestions(analysis_result),
            "risks": self._extract_risks(analysis_result),
        }

    def _gather_context(self, user_id, fund_code, fund_name, action) -> dict:
        """收集分析所需的上下文数据"""
        context = {}

        # 持仓数据
        try:
            holdings = list_holdings(user_id)
            context["holdings"] = holdings
            context["total_value"] = sum(h.get("current_value", 0) or 0 for h in holdings)
        except Exception:
            context["holdings"] = []
            context["total_value"] = 0

        # 目标基金持仓
        target_holding = next(
            (h for h in context["holdings"] if h.get("fund_code") == fund_code), None
        )
        context["target_holding"] = target_holding
        context["target_weight"] = (
            (target_holding.get("current_value", 0) or 0) / context["total_value"] * 100
            if context["total_value"] > 0 and target_holding else 0
        )

        # 估值数据
        try:
            valuation = get_latest_valuation(fund_code)
            context["valuation"] = valuation
        except Exception:
            context["valuation"] = None

        # 估值历史（近60天）
        try:
            val_history = get_valuation_history(fund_code, days=60)
            context["valuation_history"] = val_history[-10:]  # 只取最近10条
        except Exception:
            context["valuation_history"] = []

        # 近期操作历史
        try:
            recent_alerts = list_alerts(limit=50)
            fund_alerts = [
                a for a in recent_alerts
                if a.get("related_fund_code") == fund_code
                and a.get("created_at", "") >= (
                    datetime.now() - timedelta(days=30)
                ).strftime("%Y-%m-%d")
            ]
            context["recent_operations"] = fund_alerts[:5]
            context["operation_frequency"] = len(fund_alerts)
        except Exception:
            context["recent_operations"] = []
            context["operation_frequency"] = 0

        # 持仓集中度信息
        if context["total_value"] > 0:
            concentration = []
            for h in context["holdings"]:
                val = h.get("current_value", 0) or 0
                if val > 0:
                    concentration.append({
                        "fund_code": h.get("fund_code"),
                        "fund_name": h.get("fund_name"),
                        "weight_pct": round(val / context["total_value"] * 100, 2),
                    })
            concentration.sort(key=lambda x: x["weight_pct"], reverse=True)
            context["concentration_top5"] = concentration[:5]

        return context

    def _build_analysis_request(self, fund_code, fund_name, action, amount, shares, context):
        """构建发给LLM的分析请求"""
        action_map = {"add": "加仓", "reduce": "减仓", "buy": "建仓", "sell": "清仓"}

        return f"""请分析以下加减仓操作的合理性，并给出预警信号：

## 操作信息
- 基金：{fund_name}（{fund_code}）
- 操作：{action_map.get(action, action)}
- 金额：{amount}元
- 份额：{shares}

## 当前持仓概览
总资产：{context.get('total_value', 0):.2f}元
持仓数量：{len(context.get('holdings', []))}只

前5大持仓：
{json.dumps(context.get('concentration_top5', []), ensure_ascii=False, indent=2)}

## 目标基金持仓
- 当前持仓：{json.dumps(context.get('target_holding', {}), ensure_ascii=False)}
- 占总仓位比例：{context.get('target_weight', 0):.1f}%

## 估值数据
当前估值：{json.dumps(context.get('valuation', {}), ensure_ascii=False, indent=2)}

近10天估值趋势：{json.dumps(context.get('valuation_history', []), ensure_ascii=False, indent=2)}

## 近期操作
30天内对该基金的操作次数：{context.get('operation_frequency', 0)}
近期操作记录：{json.dumps(context.get('recent_operations', [])[:3], ensure_ascii=False, indent=2)}

请按系统提示词中的分析框架，给出完整的预警分析。"""

    def _call_analysis_llm(self, message: str) -> str:
        """调用LLM进行分析"""
        try:
            result = _call_llm(
                messages=[
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": message},
                ],
                temperature=0.3,
            )
            # _call_llm 可能返回 ChatCompletion 对象或字符串
            if hasattr(result, 'choices') and result.choices:
                return result.choices[0].message.content or ""
            return str(result)
        except Exception as e:
            logger.error(f"预警分析LLM调用失败: {e}")
            return f"⚠️ 分析服务暂时不可用，请稍后重试。错误：{str(e)}"

    def _extract_signal(self, analysis: str) -> str:
        """从分析结果中提取预警信号——只从信号行提取，忽略正文中的引用"""
        import re
        # 匹配「预警信号：🟢绿灯」或「预警信号：🟡 **黄灯**」等格式
        signal_pattern = r'(?:预警信号|信号等级)[：:]\s*(🟢|🟡|🔴|⚫|绿灯|黄灯|红灯|黑灯)'
        match = re.search(signal_pattern, analysis)
        if match:
            token = match.group(1)
            if token in ('⚫', '黑灯'):
                return 'black'
            elif token in ('🔴', '红灯'):
                return 'red'
            elif token in ('🟡', '黄灯'):
                return 'yellow'
            else:
                return 'green'
        # fallback：只匹配行首的信号 emoji（非正文引用）
        for line in analysis.split('\n'):
            if '预警信号' in line:
                if '⚫' in line:
                    return 'black'
                elif '🔴' in line:
                    return 'red'
                elif '🟡' in line:
                    return 'yellow'
                elif '🟢' in line:
                    return 'green'
        return 'green'

    def _extract_section(self, analysis: str, section_name: str) -> list:
        """从分析结果中提取指定章节的要点"""
        items = []
        in_section = False
        for line in analysis.split("\n"):
            if section_name in line and line.strip().startswith("#"):
                in_section = True
                continue
            if in_section:
                if line.strip().startswith("#"):
                    break
                stripped = line.strip()
                if stripped.startswith("-") or stripped.startswith("*"):
                    items.append(stripped.lstrip("- *").strip())
                elif stripped.startswith(("1.", "2.", "3.", "4.", "5.")):
                    items.append(stripped.split(".", 1)[1].strip())
        return items

    def _extract_suggestions(self, analysis: str) -> list:
        """提取操作建议"""
        return self._extract_section(analysis, "操作建议")

    def _extract_risks(self, analysis: str) -> list:
        """提取风险提示"""
        return self._extract_section(analysis, "风险提示")

    def _create_alert_notification(self, fund_code, fund_name, action, signal, analysis) -> int:
        """创建项目内预警通知"""
        action_label = {"add": "加仓", "reduce": "减仓", "buy": "建仓", "sell": "清仓"}
        signal_label = {"green": "绿灯", "yellow": "黄灯", "red": "红灯", "black": "黑灯"}
        severity_map = {"green": "info", "yellow": "warning", "red": "danger", "black": "danger"}

        title = f"{action_label[action]}预警{signal_label[signal]}：{fund_name}"

        # 截取分析结果的关键部分作为通知内容
        content_preview = analysis
        if len(content_preview) > 2000:
            content_preview = content_preview[:2000] + "\n...(已截断)"

        try:
            alert_id = create_alert(
                alert_type="position_change_alert",
                title=title,
                content=content_preview,
                severity=severity_map[signal],
                related_fund_code=fund_code,
                related_fund_name=fund_name,
                source="position_alert_agent",
            )
            return alert_id
        except Exception as e:
            logger.error(f"创建预警通知失败: {e}")
            return None

    def _create_alert_decision(self, fund_code, fund_name, action, signal, analysis) -> int:
        """为黄灯/红灯/黑灯预警自动创建决策档案"""
        try:
            signal_label = {"yellow": "黄灯预警", "red": "红灯预警", "black": "黑灯预警"}
            action_label = {"add": "加仓", "reduce": "减仓", "buy": "建仓", "sell": "清仓"}

            review_days = 30 if signal in ("red", "black") else 60

            decision_id = create_decision(
                source_type="agent_alert",
                decision_type=action,
                target_type="fund",
                target_code=fund_code,
                target_name=fund_name,
                summary=f"{signal_label[signal]}：{action_label[action]}{fund_name}",
                rationale=analysis[:500],
                review_at=(datetime.now() + timedelta(days=review_days)).strftime("%Y-%m-%d"),
            )
            return decision_id
        except Exception as e:
            logger.error(f"创建预警决策档案失败: {e}")
            return None


# 全局实例
position_alert_agent = PositionAlertAgent()
