import tempfile
import unittest
from pathlib import Path


class RagPersonalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "rag_personalization.db"

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

    def test_personalization_boost_uses_profile_and_evidence_atom_fields(self):
        from db import init_db, update_user_profile
        from rag import _apply_personalization_boost

        init_db()
        update_user_profile(
            "default",
            fund_usage="5年以上不用资金",
            primary_goal="长期增值",
            behavior_biases=["追涨", "下跌时焦虑"],
            focus_assets='["index"]',
        )

        results = [
            {
                "title": "短期题材机会",
                "body": "热门主题上涨较快，适合短线交易。",
                "content_type": "book",
                "atom_type": "case",
                "evidence_level": "general",
                "_score": 1.0,
            },
            {
                "title": "长期指数配置规则",
                "body": "长期增值资金可以用指数基金分散配置，避免追涨和下跌时焦虑，低估时分批。",
                "content_type": "book",
                "atom_type": "rule",
                "evidence_level": "principle",
                "limitations": ["适用于5年以上不用资金"],
                "counterpoints": ["如果备用金不足，应暂停加仓"],
                "_score": 1.0,
            },
        ]

        _apply_personalization_boost(results, "default")

        self.assertGreater(results[1]["personal_boost"], results[0]["personal_boost"])
        self.assertIn("fund_usage", results[1]["personal_reasons"])
        self.assertIn("behavior_bias", results[1]["personal_reasons"])
        self.assertIn("evidence_atom", results[1]["personal_reasons"])


if __name__ == "__main__":
    unittest.main()
