import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class FundDataServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "fund_data.db"

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

    def test_classify_fund_category_local(self):
        """统一分类逻辑应能正确识别常见基金类型。"""
        from db.portfolio import classify_fund_category

        self.assertEqual(classify_fund_category("某某货币基金", ""), "money_market")
        self.assertEqual(classify_fund_category("某某纯债债券", ""), "bond")
        self.assertEqual(classify_fund_category("某某沪深300指数", ""), "index")
        self.assertEqual(classify_fund_category("某某混合基金", ""), "hybrid")
        self.assertEqual(classify_fund_category("某某股票基金", ""), "equity")
        self.assertEqual(classify_fund_category("某某可转债基金", ""), "convertible_bond")

    def test_classify_uses_fund_type(self):
        """真实 fund_type 应覆盖名称启发式。"""
        from db.portfolio import classify_fund_category

        # 名称像指数但类型是债券型 → 应判为 bond
        self.assertEqual(classify_fund_category("某某中证债券指数", "债券型"), "bond")
        # 名称无特征但类型是混合型 → 应判为 hybrid
        self.assertEqual(classify_fund_category("某某优选", "混合型"), "hybrid")

    def test_save_and_get_latest_nav(self):
        """保存并读取净值历史缓存。"""
        from fund_data_service import save_latest_nav, get_fund_nav_history_from_cache

        save_latest_nav("000001", 1.2345, "2026-06-20", 0.5)
        records = get_fund_nav_history_from_cache("000001")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["nav_date"], "2026-06-20")
        self.assertAlmostEqual(records[0]["nav"], 1.2345)
        self.assertAlmostEqual(records[0]["change_pct"], 0.5)

    def test_refresh_metadata_caches_and_reads_back(self):
        """刷新基金元信息应写入 fund_metadata 并可读回。"""
        from fund_data_service import (
            refresh_fund_metadata,
            get_fund_metadata,
            _fetch_fund_metadata_from_akshare,
        )

        raw = {
            "fund_code": "000001",
            "fund_name": "测试基金",
            "fund_type": "股票型",
            "benchmark": "沪深300指数",
            "established": "2020-01-01",
        }

        with patch.object(
            fund_data_service_module := __import__("fund_data_service"),
            "_fetch_fund_metadata_from_akshare",
            return_value=raw,
        ):
            result = refresh_fund_metadata("000001")

        self.assertIsNotNone(result)
        self.assertEqual(result["fund_code"], "000001")
        self.assertEqual(result["fund_category"], "equity")

        cached = get_fund_metadata("000001")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["fund_name"], "测试基金")

    def test_get_or_refresh_nav_history_uses_cache(self):
        """本地有缓存且数据足够新时不再请求外部数据源。"""
        from fund_data_service import (
            save_latest_nav,
            get_or_refresh_fund_nav_history,
        )
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = datetime.fromtimestamp(datetime.now().timestamp() - 86400).strftime("%Y-%m-%d")

        save_latest_nav("000002", 2.0, yesterday)
        save_latest_nav("000002", 2.1, today)

        with patch("fund_data_service._fetch_nav_history_from_akshare") as mock_ak:
            records = get_or_refresh_fund_nav_history("000002", min_local_days=2)
            mock_ak.assert_not_called()

        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["nav_date"], today)

    def test_get_or_refresh_nav_history_fetches_when_stale(self):
        """本地数据过少时自动从外部获取。"""
        from fund_data_service import get_or_refresh_fund_nav_history

        fake_records = [
            {"fund_code": "000003", "nav_date": "2026-06-18", "nav": 1.0, "change_pct": None, "source": "akshare"},
            {"fund_code": "000003", "nav_date": "2026-06-19", "nav": 1.1, "change_pct": None, "source": "akshare"},
        ]

        with patch("fund_data_service._fetch_nav_history_from_akshare", return_value=fake_records):
            with patch("fund_data_service._fetch_nav_history_from_eastmoney", return_value=None):
                records = get_or_refresh_fund_nav_history("000003", min_local_days=5)

        self.assertEqual(len(records), 2)


if __name__ == "__main__":
    unittest.main()
