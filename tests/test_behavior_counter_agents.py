import tempfile
import unittest
from pathlib import Path


class BehaviorCounterAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.test_db = Path(self.tmp.name) / "agents.db"

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

    def test_specialist_seed_includes_counter_argument_agent(self):
        from db import init_db
        from db.agents import clear_specialist_cache, load_specialist_agents

        init_db()
        clear_specialist_cache()
        agents = load_specialist_agents()

        self.assertIn("behavior_coach", agents)
        self.assertIn("counter_argument", agents)
        self.assertIn("反方", agents["counter_argument"]["name"])
        self.assertIn("search_knowledge", agents["counter_argument"]["tools"])

    def test_keyword_routing_adds_behavior_and_counter_agents(self):
        from agent.orchestrator import route_to_specialists_by_keywords

        chasing = route_to_specialists_by_keywords("最近半导体涨很多，我想追涨重仓买入可以吗")
        self.assertIn("behavior_coach", chasing)
        self.assertIn("counter_argument", chasing)
        self.assertIn("risk_assessor", chasing)

        sell_panic = route_to_specialists_by_keywords("今天大跌我很慌，要不要清仓卖出")
        self.assertIn("behavior_coach", sell_panic)
        self.assertIn("counter_argument", sell_panic)

    def test_scenario_rag_map_covers_behavior_and_counter_agents(self):
        from agent.orchestrator import SCENARIO_RAG_MAP

        self.assertIn("behavior_coach", SCENARIO_RAG_MAP)
        self.assertIn("counter_argument", SCENARIO_RAG_MAP)
        self.assertIn("反例", SCENARIO_RAG_MAP["counter_argument"]["query_suffix"])


if __name__ == "__main__":
    unittest.main()
