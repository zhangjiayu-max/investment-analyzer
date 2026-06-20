import importlib
import tempfile
import unittest
from pathlib import Path


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "decisions.db"

        import db
        import db._conn as conn_mod

        self.original_conn_db_path = conn_mod.DB_PATH
        self.original_db_path = getattr(db, "DB_PATH", None)
        conn_mod.DB_PATH = self.test_db
        db.DB_PATH = self.test_db

        def restore_paths():
            conn_mod.DB_PATH = self.original_conn_db_path
            if self.original_db_path is not None:
                db.DB_PATH = self.original_db_path

        self.addCleanup(restore_paths)

    def test_create_and_list_today_decisions(self):
        from db import init_db
        from db.decisions import create_decision, list_today_decisions

        init_db()

        decision_id = create_decision(
            source_type="dashboard",
            decision_type="watch",
            target_type="index",
            target_code="000300",
            target_name="沪深300",
            summary="沪深300低估，进入观察",
            rationale="PE百分位较低，但需要确认现金计划",
            evidence={"data_points": [{"name": "PE百分位", "value": "18%"}]},
            actions=[{"action_type": "set_alert", "title": "设置沪深300低估提醒"}],
        )

        items = list_today_decisions()

        self.assertGreater(decision_id, 0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["summary"], "沪深300低估，进入观察")
        self.assertEqual(items[0]["evidence_json"]["data_points"][0]["value"], "18%")
        self.assertEqual(items[0]["actions"][0]["title"], "设置沪深300低估提醒")

    def test_update_decision_status_rejects_invalid_status(self):
        from db import init_db
        from db.decisions import create_decision, get_decision, update_decision_status

        init_db()

        decision_id = create_decision(
            source_type="chat",
            decision_type="hold",
            target_type="portfolio",
            summary="继续持有当前组合",
        )

        self.assertTrue(update_decision_status(decision_id, "accepted", "按计划执行"))
        self.assertEqual(get_decision(decision_id)["status"], "accepted")
        self.assertEqual(get_decision(decision_id)["user_note"], "按计划执行")
        self.assertFalse(update_decision_status(decision_id, "unknown"))

    def test_decision_today_api_returns_items(self):
        from db import init_db
        from db.decisions import create_decision

        init_db()
        create_decision(
            source_type="dashboard",
            decision_type="watch",
            target_type="index",
            summary="观察中证500",
        )

        import app as app_module

        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        response = client.get("/api/decisions/today")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["summary"], "观察中证500")

    def test_ensure_dashboard_decisions_creates_undervalued_watch_action_once(self):
        from db import init_db
        from db.decisions import ensure_dashboard_decisions, list_today_decisions

        init_db()

        signals = {
            "undervalued_indexes": [
                {
                    "index_code": "000300",
                    "index_name": "沪深300",
                    "percentile": 18,
                    "latest_date": "2026-06-20",
                    "assessment": "低估",
                }
            ],
            "cash_management": {
                "suggestion": {"cash_ratio": 0.18},
            },
        }

        created_first = ensure_dashboard_decisions(signals)
        created_second = ensure_dashboard_decisions(signals)
        items = list_today_decisions()

        self.assertEqual(created_first, 1)
        self.assertEqual(created_second, 0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["target_name"], "沪深300")
        self.assertEqual(items[0]["decision_type"], "watch")
        self.assertIn("沪深300", items[0]["summary"])
        self.assertEqual(items[0]["evidence_json"]["data_points"][0]["value"], "18%")

    def test_ensure_dashboard_decisions_creates_cash_opportunity_when_cash_is_high(self):
        from db import init_db
        from db.decisions import ensure_dashboard_decisions, list_today_decisions

        init_db()

        signals = {
            "undervalued_indexes": [
                {
                    "index_code": "000300",
                    "index_name": "沪深300",
                    "percentile": 16,
                    "latest_date": "2026-06-20",
                    "assessment": "低估",
                },
                {
                    "index_code": "000905",
                    "index_name": "中证500",
                    "percentile": 22,
                    "latest_date": "2026-06-20",
                    "assessment": "低估",
                },
            ],
            "cash_management": {
                "balance": 180000,
                "suggestion": {
                    "cash_ratio": 0.26,
                    "summary": "现金占比偏高，可逐步配置",
                },
            },
            "portfolio_health": {
                "total_value": 510000,
                "concentration_level": "low",
            },
        }

        created_first = ensure_dashboard_decisions(signals)
        created_second = ensure_dashboard_decisions(signals)
        items = list_today_decisions()
        cash_items = [item for item in items if item["decision_type"] == "add" and item["target_type"] == "cash"]

        self.assertEqual(created_first, 3)
        self.assertEqual(created_second, 0)
        self.assertEqual(len(cash_items), 1)
        self.assertIn("现金占比 26%", cash_items[0]["summary"])
        self.assertEqual(cash_items[0]["confidence"], "medium")
        self.assertEqual(cash_items[0]["suitability_json"]["cash_ratio"], "26%")
        self.assertIn("沪深300", cash_items[0]["evidence_json"]["portfolio_context"]["opportunity_names"])
        self.assertIn("先确认 3-6 个月备用金", cash_items[0]["evidence_json"]["counter_arguments"][0])
        self.assertEqual(cash_items[0]["actions"][0]["action_type"], "review_cash_plan")

    def test_ensure_dashboard_decisions_creates_rebalance_review_for_high_concentration(self):
        from db import init_db
        from db.decisions import ensure_dashboard_decisions, list_today_decisions

        init_db()

        signals = {
            "undervalued_indexes": [],
            "cash_management": {
                "suggestion": {"cash_ratio": 0.08},
            },
            "portfolio_health": {
                "holding_count": 18,
                "total_value": 690554.43,
                "top3_concentration": 70.2,
                "max_holding_pct": 31.4,
                "concentration_level": "high",
                "concentration_assessment": "前3持仓占比 70.2%，集中度很高，建议分散",
            },
        }

        created_first = ensure_dashboard_decisions(signals)
        created_second = ensure_dashboard_decisions(signals)
        items = list_today_decisions()

        self.assertEqual(created_first, 1)
        self.assertEqual(created_second, 0)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["decision_type"], "rebalance")
        self.assertEqual(items[0]["target_type"], "portfolio")
        self.assertIn("前3持仓占比 70.2%", items[0]["summary"])
        self.assertEqual(items[0]["risk_json"]["level"], "medium")
        self.assertEqual(items[0]["evidence_json"]["data_points"][0]["value"], "70.2%")
        self.assertEqual(items[0]["actions"][0]["action_type"], "review_rebalance")

    def test_decision_review_flow_lists_due_items_and_records_review(self):
        from db import init_db
        from db.decisions import (
            create_decision,
            get_decision,
            list_due_decision_reviews,
            record_decision_review,
            update_decision_status,
        )

        init_db()
        due_id = create_decision(
            source_type="dashboard",
            decision_type="add",
            target_type="cash",
            target_name="零钱配置",
            summary="现金占比偏高，制定分批配置计划",
            review_at="2000-01-01",
        )
        future_id = create_decision(
            source_type="dashboard",
            decision_type="watch",
            target_type="index",
            target_name="沪深300",
            summary="沪深300低估观察",
            review_at="2999-01-01",
        )
        update_decision_status(due_id, "accepted")
        update_decision_status(future_id, "accepted")

        due_items = list_due_decision_reviews()
        review_id = record_decision_review(
            due_id,
            outcome="helpful",
            result_note="分批计划保留了足够备用金",
            profit_change=128.5,
            lesson="以后现金超过 20% 时先做资金分层",
        )
        reviewed = get_decision(due_id)

        self.assertEqual([item["id"] for item in due_items], [due_id])
        self.assertGreater(review_id, 0)
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertEqual(reviewed["review"]["outcome"], "helpful")
        self.assertEqual(reviewed["review"]["profit_change"], 128.5)
        self.assertEqual(reviewed["review"]["lesson"], "以后现金超过 20% 时先做资金分层")

    def test_decision_review_api_lists_due_items_and_accepts_review(self):
        from db import init_db
        from db.decisions import create_decision, update_decision_status

        init_db()
        decision_id = create_decision(
            source_type="dashboard",
            decision_type="rebalance",
            target_type="portfolio",
            target_name="整体组合",
            summary="前3持仓集中度偏高",
            review_at="2000-01-01",
        )
        update_decision_status(decision_id, "accepted")

        import app as app_module

        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        list_response = client.get("/api/decisions/reviews/due")
        review_response = client.post(
            f"/api/decisions/{decision_id}/review",
            json={
                "outcome": "neutral",
                "result_note": "只是完成了检查，暂不调整",
                "profit_change": 0,
                "lesson": "集中度高不一定要立刻卖出",
            },
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["items"][0]["id"], decision_id)
        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_response.json()["item"]["status"], "reviewed")
        self.assertEqual(review_response.json()["item"]["review"]["outcome"], "neutral")

    def test_create_chat_decision_draft_extracts_checklist_and_review_date(self):
        from db import init_db
        from db.conversations import create_conversation, create_message
        from db.decisions import create_chat_decision_draft, get_decision

        init_db()
        conv_id = create_conversation("沪深300加仓讨论")
        user_msg_id = create_message(conv_id, "user", "沪深300现在低估，可以加仓吗？")
        assistant_msg_id = create_message(
            conv_id,
            "assistant",
            "结论：可以小额分批加仓沪深300，但不要一次性买入。理由是PE百分位约18%，处于低估区。"
            "风险是短期仍可能继续下跌，执行前要确认备用金和仓位上限。",
        )

        draft_id = create_chat_decision_draft(
            conversation_id=conv_id,
            assistant_message_id=assistant_msg_id,
            user_message_id=user_msg_id,
            assistant_content="结论：可以小额分批加仓沪深300，但不要一次性买入。理由是PE百分位约18%，处于低估区。"
                              "风险是短期仍可能继续下跌，执行前要确认备用金和仓位上限。",
            user_query="沪深300现在低估，可以加仓吗？",
            target_name="沪深300",
            target_type="index",
        )
        item = get_decision(draft_id)

        self.assertGreater(draft_id, 0)
        self.assertEqual(item["source_type"], "chat")
        self.assertEqual(item["source_id"], assistant_msg_id)
        self.assertEqual(item["decision_type"], "add")
        self.assertEqual(item["target_name"], "沪深300")
        self.assertEqual(item["status"], "proposed")
        self.assertTrue(item["review_at"])
        self.assertIn("PE百分位约18%", item["evidence_json"]["data_points"][0]["value"])
        self.assertEqual(item["evidence_json"]["source"]["conversation_id"], conv_id)
        self.assertIn("短期仍可能继续下跌", item["risk_json"]["counter_arguments"][0])
        self.assertIn("确认备用金", item["suitability_json"]["checklist"][0])
        self.assertEqual(item["actions"][0]["action_type"], "pre_trade_check")

    def test_chat_decision_draft_api_creates_decision_from_message(self):
        from db import init_db
        from db.conversations import create_conversation, create_message

        init_db()
        conv_id = create_conversation("中证500观察")
        user_msg_id = create_message(conv_id, "user", "中证500可以买吗？")
        assistant_msg_id = create_message(
            conv_id,
            "assistant",
            "建议先观察中证500，估值有吸引力但需要等待更明确的资金计划。"
        )

        import app as app_module

        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        response = client.post(
            "/api/decisions/from-chat",
            json={
                "conversation_id": conv_id,
                "assistant_message_id": assistant_msg_id,
                "user_message_id": user_msg_id,
                "target_name": "中证500",
                "target_type": "index",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["item"]["source_id"], assistant_msg_id)
        self.assertEqual(body["item"]["target_name"], "中证500")
        self.assertEqual(body["item"]["actions"][0]["action_type"], "pre_trade_check")

    def test_review_lesson_is_saved_as_user_lesson_knowledge(self):
        from db import init_db
        from db.decisions import create_decision, record_decision_review
        from db.knowledge import search_knowledge

        init_db()
        decision_id = create_decision(
            source_type="chat",
            decision_type="add",
            target_type="index",
            target_name="沪深300",
            summary="沪深300分批加仓草案",
        )

        record_decision_review(
            decision_id,
            outcome="helpful",
            result_note="分批执行降低了波动影响",
            profit_change=120.0,
            lesson="以后低估加仓前，先确认备用金和单次仓位上限",
        )
        lessons = search_knowledge("备用金", category="user_lesson", limit=5)

        self.assertEqual(len(lessons), 1)
        self.assertIn("沪深300", lessons[0]["title"])
        self.assertIn("先确认备用金", lessons[0]["content"])
        self.assertEqual(lessons[0]["source"], f"decision_review:{decision_id}")

    def test_decision_pre_trade_check_api_returns_blockers_from_profile(self):
        from db import init_db, update_user_profile
        from db.decisions import create_decision

        init_db()
        update_user_profile(
            "default",
            emergency_fund_months=1,
            monthly_surplus=3000,
            target_equity_ratio=0.4,
            max_single_position_pct=0.1,
        )
        decision_id = create_decision(
            source_type="chat",
            decision_type="add",
            target_type="portfolio",
            summary="加仓权益资产",
            evidence={"missing_data": ["目标仓位"]},
            suitability={"checklist": ["确认备用金"]},
        )

        import app as app_module

        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        response = client.get(f"/api/decisions/{decision_id}/precheck")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok_to_execute"])
        self.assertIn("备用金不足", body["blockers"][0])
        self.assertIn("确认备用金", body["checklist"])

    def test_decision_precheck_blocks_buy_from_emergency_bucket(self):
        from db import init_db, update_user_profile
        from db.decisions import build_decision_precheck, create_decision
        from db.goal_buckets import create_goal_bucket

        init_db()
        update_user_profile("default", emergency_fund_months=6, monthly_surplus=8000)
        bucket_id = create_goal_bucket(
            name="家庭备用金",
            bucket_type="emergency",
            target_amount=60000,
            current_amount=60000,
            risk_level="very_low",
            liquidity_days=1,
            priority=1,
        )
        decision_id = create_decision(
            source_type="chat",
            decision_type="buy",
            target_type="index",
            target_code="000300",
            target_name="沪深300",
            summary="用备用金买入沪深300",
            suitability={"source_bucket_id": bucket_id},
        )

        result = build_decision_precheck(decision_id)

        self.assertFalse(result["ok_to_execute"])
        self.assertIn("家庭备用金", result["blockers"][0])
        self.assertIn("备用金桶", result["blockers"][0])
        self.assertEqual(result["source_bucket"]["bucket_type"], "emergency")

    def test_profile_api_accepts_financial_profile_v2_fields(self):
        from db import init_db

        init_db()

        import app as app_module

        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        update_response = client.put(
            "/api/profile",
            json={
                "monthly_income": 30000,
                "monthly_expense": 12000,
                "monthly_surplus": 18000,
                "emergency_fund_months": 6,
                "target_equity_ratio": 0.55,
                "max_single_position_pct": 0.12,
                "primary_goal": "长期增值",
                "fund_usage": "5年以上不用资金",
                "liquidity_needs": "低",
                "liabilities_summary": "房贷每月8000",
                "behavior_biases": ["追涨", "下跌时焦虑"],
            },
        )
        get_response = client.get("/api/profile")

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(get_response.status_code, 200)
        profile = get_response.json()
        self.assertEqual(profile["monthly_income"], 30000)
        self.assertEqual(profile["emergency_fund_months"], 6)
        self.assertEqual(profile["behavior_biases"], ["追涨", "下跌时焦虑"])

    def test_unhelpful_decision_review_creates_eval_case(self):
        from db import init_db
        from db.decisions import create_decision, record_decision_review
        from db.eval import list_eval_cases

        init_db()
        decision_id = create_decision(
            source_type="chat",
            decision_type="add",
            target_type="index",
            target_name="中证500",
            summary="中证500加仓草案",
            rationale="低估但缺少备用金检查",
        )

        record_decision_review(
            decision_id,
            outcome="unhelpful",
            result_note="建议没有考虑我的短期用钱需求",
            lesson="以后新增仓位前必须先问资金用途",
        )
        cases = list_eval_cases(analysis_type="decision_add")

        self.assertEqual(len(cases), 1)
        self.assertIn("中证500", cases[0]["name"])
        self.assertIn("资金用途", cases[0]["expected_quality"])


if __name__ == "__main__":
    unittest.main()
