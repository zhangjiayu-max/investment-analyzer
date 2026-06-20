import importlib
import tempfile
import unittest
from pathlib import Path


class StressTestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "stress.db"

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
            fund_code="510300",
            fund_name="沪深300ETF",
            shares=10000,
            cost_price=1,
            current_price=1,
            fund_category="index",
        )
        create_holding(
            fund_code="110000",
            fund_name="中短债基金",
            shares=20000,
            cost_price=1,
            current_price=1,
            fund_category="bond",
        )
        set_cash_balance("default", 10000)
        create_goal_bucket(
            name="家庭备用金",
            bucket_type="emergency",
            target_amount=60000,
            current_amount=10000,
            risk_level="very_low",
            liquidity_days=1,
            priority=1,
        )

    def test_market_drop_stress_test_estimates_loss_and_buffers(self):
        self._seed_portfolio()
        from stress_test import run_portfolio_stress_test

        result = run_portfolio_stress_test("market_drop_20")

        self.assertEqual(result["scenario"], "market_drop_20")
        self.assertEqual(result["status"], "ok")
        self.assertLess(result["projected_total_assets"], result["total_assets"])
        self.assertGreater(result["loss_amount"], 0)
        self.assertEqual(result["emergency_bucket"]["name"], "家庭备用金")
        self.assertIn("备用金不足", result["warnings"][0])
        self.assertTrue(result["asset_impacts"])

    def test_stress_test_api(self):
        self._seed_portfolio()

        import app as app_module

        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        response = client.post("/api/portfolio/stress-test", json={"scenario": "market_drop_20"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIn("asset_impacts", response.json())


if __name__ == "__main__":
    unittest.main()
