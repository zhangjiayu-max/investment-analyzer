import importlib
import tempfile
import unittest
from pathlib import Path


class AllocationDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "allocation.db"

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

    def _seed_portfolio(self):
        from db import create_goal_bucket, create_holding, init_db, set_cash_balance

        init_db()
        create_holding(
            fund_code="000001",
            fund_name="沪深300ETF",
            shares=10000,
            cost_price=1,
            current_price=1,
            fund_category="index",
        )
        create_holding(
            fund_code="000002",
            fund_name="中短债基金",
            shares=20000,
            cost_price=1,
            current_price=1,
            fund_category="bond",
        )
        set_cash_balance("default", 30000)
        create_goal_bucket(
            name="家庭备用金",
            bucket_type="emergency",
            target_amount=60000,
            current_amount=30000,
            risk_level="very_low",
            liquidity_days=1,
            priority=1,
        )

    def test_allocation_dashboard_builds_rows_and_guardrails(self):
        self._seed_portfolio()
        from allocation_dashboard import build_allocation_dashboard

        result = build_allocation_dashboard()

        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(result["total_assets"], 60000.0)
        self.assertGreaterEqual(len(result["allocation_rows"]), 3)
        cash_row = next(row for row in result["allocation_rows"] if row["category"] == "cash")
        self.assertGreaterEqual(cash_row["current_amount"], 30000.0)
        self.assertIn("drift_abs", cash_row)
        self.assertEqual(result["goal_constraints"]["emergency_bucket"]["name"], "家庭备用金")
        self.assertIn("备用金桶未达标", result["guardrails"][0])
        self.assertTrue(result["suggestions"])

    def test_allocation_dashboard_api(self):
        self._seed_portfolio()

        import app as app_module

        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        response = client.get("/api/portfolio/allocation-dashboard")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("allocation_rows", body)
        self.assertIn("goal_constraints", body)


if __name__ == "__main__":
    unittest.main()
