import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PortfolioTransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "portfolio.db"

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

        from db import init_db

        init_db()

    def test_confirm_new_buy_creates_holding_without_double_counting(self):
        from db import confirm_transaction, create_transaction, get_holding_by_fund

        tx_id = create_transaction(
            fund_code="000001",
            fund_name="测试基金",
            transaction_type="buy",
            amount=0,
            submitted_amount=1000,
            transaction_date="2026-06-18",
            status="pending",
            account="花无缺",
        )

        self.assertTrue(confirm_transaction(tx_id, confirmed_price=2.0))

        holding = get_holding_by_fund("000001")
        self.assertIsNotNone(holding)
        self.assertEqual(holding["shares"], 500)
        self.assertEqual(holding["total_cost"], 1000)

    def test_auto_confirm_due_buy_uses_trade_day_nav_and_updates_holding(self):
        from db import create_holding, create_transaction, get_holding
        from db.portfolio import auto_confirm_due_transactions

        holding_id = create_holding(
            fund_code="000002",
            fund_name="已有基金",
            shares=100,
            cost_price=1,
            current_price=1,
        )
        create_transaction(
            fund_code="000002",
            holding_id=holding_id,
            transaction_type="buy",
            amount=0,
            submitted_amount=1000,
            transaction_date="2026-06-18",
            transaction_time="14:30",
            expected_confirm_date="2026-06-19",
            status="pending",
        )

        with patch("db.portfolio.fetch_fund_nav_on_or_before", return_value={"nav": 2.0, "date": "2026-06-18"}):
            result = auto_confirm_due_transactions(as_of_date="2026-06-19")

        self.assertEqual(result["confirmed"], 1)
        holding = get_holding(holding_id)
        self.assertEqual(holding["shares"], 600)
        self.assertEqual(holding["total_cost"], 1100)


if __name__ == "__main__":
    unittest.main()
