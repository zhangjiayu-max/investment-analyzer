import importlib
import tempfile
import unittest
from pathlib import Path


class GoalBucketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "goal_buckets.db"

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

    def test_goal_bucket_crud_and_summary(self):
        from db import init_db
        from db.goal_buckets import (
            create_goal_bucket,
            delete_goal_bucket,
            get_goal_bucket,
            get_goal_bucket_summary,
            list_goal_buckets,
            update_goal_bucket,
        )

        init_db()

        emergency_id = create_goal_bucket(
            name="家庭备用金",
            bucket_type="emergency",
            target_amount=60000,
            current_amount=42000,
            target_ratio=0.12,
            risk_level="very_low",
            liquidity_days=1,
            priority=1,
            notes="3-6 个月生活费，只放现金类资产",
        )
        growth_id = create_goal_bucket(
            name="长期增值",
            bucket_type="long_term",
            target_amount=300000,
            current_amount=120000,
            target_ratio=0.65,
            risk_level="medium_high",
            liquidity_days=1095,
            priority=3,
            notes="3 年以上不用资金",
        )

        self.assertGreater(emergency_id, 0)
        self.assertGreater(growth_id, 0)

        items = list_goal_buckets()
        self.assertEqual([item["name"] for item in items], ["家庭备用金", "长期增值"])
        self.assertEqual(items[0]["progress_pct"], 70.0)
        self.assertEqual(items[0]["guardrail_level"], "blocked_for_risk_assets")
        self.assertEqual(items[1]["guardrail_level"], "risk_assets_allowed")

        self.assertTrue(update_goal_bucket(growth_id, current_amount=180000, notes="每季度再平衡"))
        updated = get_goal_bucket(growth_id)
        self.assertEqual(updated["current_amount"], 180000.0)
        self.assertEqual(updated["progress_pct"], 60.0)
        self.assertEqual(updated["notes"], "每季度再平衡")

        summary = get_goal_bucket_summary()
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["total_current_amount"], 222000.0)
        self.assertEqual(summary["type_counts"]["emergency"], 1)
        self.assertEqual(summary["emergency_bucket"]["name"], "家庭备用金")

        self.assertTrue(delete_goal_bucket(emergency_id))
        self.assertIsNone(get_goal_bucket(emergency_id))
        self.assertEqual(len(list_goal_buckets()), 1)

    def test_goal_bucket_api_crud(self):
        from db import init_db

        init_db()
        import app as app_module

        importlib.reload(app_module)

        from fastapi.testclient import TestClient

        client = TestClient(app_module.app)
        create_response = client.post(
            "/api/profile/buckets",
            json={
                "name": "机会资金",
                "bucket_type": "opportunity",
                "target_amount": 80000,
                "current_amount": 15000,
                "target_ratio": 0.08,
                "risk_level": "medium",
                "liquidity_days": 180,
                "priority": 4,
                "notes": "只用于低估试投",
            },
        )

        self.assertEqual(create_response.status_code, 200)
        bucket_id = create_response.json()["id"]

        list_response = client.get("/api/profile/buckets")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["items"][0]["name"], "机会资金")
        self.assertEqual(list_response.json()["summary"]["count"], 1)

        update_response = client.put(
            f"/api/profile/buckets/{bucket_id}",
            json={"current_amount": 22000, "notes": "低估时分三笔"},
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["item"]["current_amount"], 22000.0)

        delete_response = client.delete(f"/api/profile/buckets/{bucket_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["ok"])


if __name__ == "__main__":
    unittest.main()
