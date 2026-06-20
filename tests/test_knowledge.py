import tempfile
import unittest
from pathlib import Path


class KnowledgeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "knowledge.db"

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

    def test_add_knowledge_persists_evidence_atom_metadata(self):
        from db import init_db
        from db.knowledge import add_knowledge, get_knowledge

        init_db()
        knowledge_id = add_knowledge(
            category="user_lesson",
            subcategory="buy_decision",
            title="低估加仓前检查备用金",
            content="以后低估加仓前，先确认备用金和单次仓位上限。",
            source="decision_review:1",
            keywords=["备用金", "仓位"],
            importance=8,
            atom_type="user_lesson",
            evidence_level="user_memory",
            as_of_date="2026-06-20",
            valid_until="2027-06-20",
            limitations=["只适用于新增风险资产仓位"],
            counterpoints=["若资金已明确长期不用，可放宽分批周期"],
        )
        item = get_knowledge(knowledge_id)

        self.assertEqual(item["atom_type"], "user_lesson")
        self.assertEqual(item["evidence_level"], "user_memory")
        self.assertEqual(item["as_of_date"], "2026-06-20")
        self.assertEqual(item["valid_until"], "2027-06-20")
        self.assertEqual(item["limitations"], ["只适用于新增风险资产仓位"])
        self.assertEqual(item["counterpoints"], ["若资金已明确长期不用，可放宽分批周期"])


if __name__ == "__main__":
    unittest.main()
