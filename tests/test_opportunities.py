import tempfile
import unittest
from pathlib import Path


class OpportunityEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "opportunities.db"

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

        from db import init_db, save_valuation, set_cash_balance

        init_db()
        set_cash_balance("default", 50000)
        save_valuation({
            "index_code": "000922",
            "index_name": "中证红利",
            "snapshot_date": "2026-06-22",
            "metric_type": "市盈率",
            "current_value": 7.8,
            "percentile": 38,
        })

    def test_daily_scan_generates_policy_backed_plan_with_otc_fee_guardrail(self):
        from opportunity_engine import scan_daily_opportunities

        result = scan_daily_opportunities(
            news_items=[
                {
                    "title": "政策支持上市公司分红质量提升，红利资产受关注",
                    "summary": "新国九条后高股息和红利低波方向持续获得资金关注。",
                    "source": "test",
                }
            ],
            trade_date="2026-06-22",
            user_id="default",
        )

        self.assertEqual(result["date"], "2026-06-22")
        self.assertGreaterEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["theme"], "红利低波")
        self.assertGreaterEqual(item["opportunity_score"], 50)
        self.assertIn("政策", item["policy_signal"])
        self.assertIn("退出", item["exit_plan"]["time_stop"])
        self.assertEqual(item["matched_funds"][0]["fund_code"], "009051")
        self.assertEqual(item["matched_funds"][0]["vehicle_type"], "otc_fund")
        self.assertIn("7天", item["matched_funds"][0]["fee_warning"])
        self.assertEqual(item["verdict"], "watch")

    def test_create_decision_from_opportunity_persists_actions(self):
        from db.opportunities import create_decision_from_opportunity, get_opportunity
        from db.decisions import get_decision
        from opportunity_engine import scan_daily_opportunities

        result = scan_daily_opportunities(
            news_items=[{"title": "红利政策持续发酵", "summary": "高股息资产获得关注", "source": "test"}],
            trade_date="2026-06-22",
        )
        opportunity_id = result["items"][0]["id"]

        decision_id = create_decision_from_opportunity(opportunity_id)

        decision = get_decision(decision_id)
        self.assertEqual(decision["source_type"], "opportunity")
        self.assertEqual(decision["source_id"], opportunity_id)
        self.assertTrue(decision["actions"])
        self.assertIn("复盘", decision["actions"][1]["title"])
        self.assertEqual(get_opportunity(opportunity_id)["status"], "watching")

    def test_mark_bought_keeps_transaction_id_separate_from_decision_id(self):
        from db import _get_conn
        from db.opportunities import get_opportunity, mark_opportunity_bought
        from opportunity_engine import scan_daily_opportunities

        result = scan_daily_opportunities(
            news_items=[{"title": "红利政策持续发酵", "summary": "高股息资产获得关注", "source": "test"}],
            trade_date="2026-06-22",
        )
        opportunity_id = result["items"][0]["id"]

        track_id = mark_opportunity_bought(
            opportunity_id,
            fund_code="009051",
            amount=1000,
            transaction_id=888,
        )

        conn = _get_conn()
        row = conn.execute("SELECT * FROM theme_opportunity_tracks WHERE id = ?", (track_id,)).fetchone()
        conn.close()
        self.assertEqual(row["transaction_id"], 888)
        self.assertIsNone(row["decision_id"])
        self.assertEqual(get_opportunity(opportunity_id)["status"], "bought")

    def test_opportunity_api_scans_and_creates_decision(self):
        import importlib

        import app as app_module
        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        response = client.post("/api/opportunities/daily-scan", json={
            "force_refresh": True,
            "news_items": [
                {
                    "title": "政策推动红利资产重估",
                    "summary": "分红质量提升带动高股息方向关注",
                    "source": "test",
                }
            ],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreaterEqual(len(body["items"]), 1)

        opportunity_id = body["items"][0]["id"]
        decision_response = client.post(f"/api/opportunities/{opportunity_id}/create-decision")
        self.assertEqual(decision_response.status_code, 200)
        self.assertGreater(decision_response.json()["decision_id"], 0)


if __name__ == "__main__":
    unittest.main()
