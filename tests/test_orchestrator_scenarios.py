import unittest


class OrchestratorScenarioTests(unittest.TestCase):
    def test_detects_decision_scenarios(self):
        from agent.orchestrator import detect_scenario_type

        self.assertEqual(detect_scenario_type("沪深300现在可以加仓吗？"), "buy_decision")
        self.assertEqual(detect_scenario_type("这只基金要不要卖出止盈？"), "sell_decision")
        self.assertEqual(detect_scenario_type("帮我看看当前持仓是否太集中"), "portfolio_review")
        self.assertEqual(detect_scenario_type("分析这篇文章观点是否靠谱 https://example.com/a"), "article_check")
        self.assertEqual(detect_scenario_type("复盘一下上次中证500决策"), "decision_review")
        self.assertEqual(detect_scenario_type("什么是股债再平衡？"), "knowledge_qa")


if __name__ == "__main__":
    unittest.main()
