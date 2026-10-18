import os
import unittest
from unittest.mock import MagicMock, patch

ENV_DEFAULTS = {
    "BRAINPAT_TOKEN": "test-token",
    "MODELS_MODE": "local",
    "EMBEDDINGS_LOCAL_MODEL": "local-model",
    "EMBEDDINGS_SMALL_MODEL": "small-model",
    "EMBEDDING_NODES_DIMENSION": "3",
    "EMBEDDING_TRIPLETS_DIMENSION": "3",
    "EMBEDDING_OBSERVATIONS_DIMENSION": "3",
    "EMBEDDING_DATA_DIMENSION": "3",
    "EMBEDDING_RELATIONSHIPS_DIMENSION": "3",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "NEO4J_HOST": "localhost",
    "NEO4J_PORT": "7687",
    "NEO4J_USERNAME": "neo4j",
    "NEO4J_PASSWORD": "password",
    "MILVUS_HOST": "localhost",
    "MILVUS_PORT": "19530",
    "MONGO_CONNECTION_STRING": "mongodb://localhost:27017",
    "CELERY_WORKER_CONCURRENCY": "1",
    "OLLAMA_HOST": "localhost",
    "OLLAMA_PORT": "11434",
    "OLLAMA_LLM_SMALL_MODEL": "small",
    "OLLAMA_LLM_LARGE_MODEL": "large",
}
for key, value in ENV_DEFAULTS.items():
    os.environ.setdefault(key, value)

from src.core.search.bm25 import okapi_bm25_document
from src.constants.data import TextChunk
from src.lib.postgresql import data as pgdata


def _postgres_available() -> bool:
    try:
        import psycopg2

        pg = pgdata.config.postgresql
        pg.validate_credentials()
        conn = psycopg2.connect(
            host=pg.host,
            port=pg.port,
            user=pg.username,
            password=pg.password,
            dbname=pg.maintenance_database or "postgres",
            connect_timeout=2,
        )
        conn.close()
        return True
    except Exception:
        return False


class OkapiBm25Tests(unittest.TestCase):
    def setUp(self):
        # These unit tests isolate FTS schema policy for registered namespaces.
        for module in ('src.lib.postgresql.data', 'src.lib.postgresql.graph_store'):
            guard = patch(module + '.require_registered_brain')
            guard.start()
            self.addCleanup(guard.stop)

    def test_term_overlap_ranks_above_non_overlap(self):
        overlap = okapi_bm25_document(
            {"license": 2.0},
            dl=8,
            avgdl=8,
            n_docs=2,
            dfs={"license": 1},
        )
        non_overlap = okapi_bm25_document(
            {},
            dl=8,
            avgdl=8,
            n_docs=2,
            dfs={"license": 1},
        )
        self.assertGreater(overlap, non_overlap)
        self.assertEqual(non_overlap, 0.0)

    def test_higher_tf_ranks_higher(self):
        high = okapi_bm25_document(
            {"license": 3.0},
            dl=10,
            avgdl=10,
            n_docs=4,
            dfs={"license": 2},
        )
        low = okapi_bm25_document(
            {"license": 1.0},
            dl=10,
            avgdl=10,
            n_docs=4,
            dfs={"license": 2},
        )
        self.assertGreater(high, low)

    def test_search_ddl_is_gated_off_base_schema(self):
        self.assertNotIn("search_tsv", pgdata._BRAIN_DDL)
        self.assertIn("search_tsv", pgdata._SEARCH_DDL)
        self.assertIn("gin", pgdata._SEARCH_DDL.lower())
        self.assertIn("to_tsvector", pgdata._SEARCH_DDL)
        self.assertIn("to_tsvector('english'", pgdata._SEARCH_DDL)
        self.assertNotIn("to_tsvector('italian'", pgdata._SEARCH_DDL)
        self.assertNotIn("to_tsvector('spanish'", pgdata._SEARCH_DDL)
        self.assertNotIn("search_tsv_alt", pgdata._SEARCH_DDL)
        alt = pgdata._search_alt_ddl("italian")
        self.assertIn("to_tsvector('italian'", alt)
        self.assertIn("search_tsv_alt", alt)
        with self.assertRaises(ValueError):
            pgdata._search_alt_ddl("english")

    def test_english_bm25_sql_keeps_plainto_and(self):
        sql = pgdata._bm25_sql("english", "search_tsv", "search_len")
        self.assertIn("plainto_tsquery('english'", sql)
        self.assertIn("search_tsv", sql)
        self.assertNotIn("search_tsv_alt", sql)
        self.assertNotIn("WHERE TRUE", sql)

    def test_alt_bm25_sql_matches_any_lexeme(self):
        sql = pgdata._bm25_sql("italian", "search_tsv_alt", "search_len_alt")
        self.assertNotIn("plainto_tsquery", sql)
        self.assertIn("WHERE TRUE", sql)
        self.assertIn("search_tsv_alt", sql)
        self.assertIn("search_len_alt", sql)
        and_sql = pgdata._bm25_sql(
            "italian", "search_tsv_alt", "search_len_alt", query_match="and"
        )
        self.assertIn("plainto_tsquery('italian'", and_sql)

    def test_searchbenchesci74_stays_english_and_path(self):
        with (
            patch.object(
                pgdata.config, "search_fts_brains", frozenset({"searchbenchitsmoke"})
            ),
            patch.object(pgdata.config, "search_fts_regconfig", "italian"),
        ):
            self.assertIsNone(
                pgdata.config.search_fts_regconfig_for_brain("searchbenchesci74")
            )
            self.assertIsNone(
                pgdata.config.search_fts_regconfig_for_brain("locomoconv26")
            )

    def test_node_search_schema_skipped_when_search_disabled(self):
        from src.lib.postgresql.graph_store import PostgreSQLGraphStore

        store = PostgreSQLGraphStore.__new__(PostgreSQLGraphStore)
        store._schema_ready = {"brain-a"}
        store._search_ready_brains = set()
        store._schema_lock = MagicMock()
        with patch.object(pgdata.config, "search_enabled", False):
            store._ensure_brain_schema("brain-a")
        self.assertEqual(store._search_ready_brains, set())
        store._schema_lock.assert_not_called()

    def test_search_schema_skipped_when_search_disabled(self):
        client = pgdata.PostgreSQLDataClient.__new__(pgdata.PostgreSQLDataClient)
        client._initialized_brains = {"brain-a"}
        client._search_ready_brains = set()
        client._search_alt_brains = set()
        client._lock = MagicMock()
        with patch.object(pgdata.config, "search_enabled", False):
            client._ensure_brain_schema("brain-a")
        self.assertEqual(client._search_ready_brains, set())

    @unittest.skipUnless(_postgres_available(), "postgres not available")
    def test_postgres_bm25_ranks_term_overlap_first(self):
        brain_id = "searchbench_bm25_unit"
        with patch.object(pgdata.config, "search_enabled", True):
            client = pgdata.PostgreSQLDataClient()
            if client.get_brain(brain_id) is None:
                client.create_brain(brain_id)
            overlap = TextChunk(
                id="overlap",
                text="alice counseling license renewal board",
            )
            other = TextChunk(
                id="other",
                text="weather forecast sunny tomorrow picnic",
            )
            client.save_text_chunk(overlap, brain_id)
            client.save_text_chunk(other, brain_id)
            ranked = client.search_bm25("counseling license", brain_id, limit=10)
        ids = [chunk.id for chunk, _score in ranked]
        self.assertTrue(ids, "expected at least one BM25 hit")
        self.assertEqual(ids[0], "overlap")
        self.assertNotIn("other", ids)

    def test_fts_regconfig_only_allowlisted_searchbench(self):
        with (
            patch.object(
                pgdata.config,
                "search_fts_brains",
                frozenset({"locomoconv26", "searchbenchitsmoke", "demorecsys"}),
            ),
            patch.object(pgdata.config, "search_fts_regconfig", "italian"),
        ):
            self.assertEqual(
                pgdata.config.search_fts_regconfig_for_brain("searchbenchitsmoke"),
                "italian",
            )
            self.assertIsNone(
                pgdata.config.search_fts_regconfig_for_brain("searchbenchescies")
            )
            self.assertIsNone(pgdata.config.search_fts_regconfig_for_brain("locomoconv26"))
            self.assertIsNone(pgdata.config.search_fts_regconfig_for_brain("demorecsys"))
            self.assertIsNone(pgdata.config.search_fts_regconfig_for_brain("beam1m1clean"))

    def test_alt_ddl_not_executed_for_memory_brain(self):
        client = pgdata.PostgreSQLDataClient.__new__(pgdata.PostgreSQLDataClient)
        client._initialized_brains = {"locomoconv26"}
        client._search_ready_brains = {"locomoconv26"}
        client._search_alt_brains = set()
        client._lock = MagicMock()
        with (
            patch.object(pgdata.config, "search_enabled", True),
            patch.object(
                pgdata.config, "search_fts_regconfig_for_brain", return_value=None
            ),
            patch.object(pgdata, "ensure_brain_database") as ensure_db,
        ):
            client._ensure_brain_schema("locomoconv26")
        ensure_db.assert_not_called()
        self.assertEqual(client._search_alt_brains, set())

    def test_alt_ddl_executed_for_allowlisted_searchbench(self):
        from contextlib import contextmanager

        client = pgdata.PostgreSQLDataClient.__new__(pgdata.PostgreSQLDataClient)
        client._initialized_brains = {"searchbenchitsmoke"}
        client._search_ready_brains = {"searchbenchitsmoke"}
        client._search_alt_brains = set()
        client._lock = MagicMock()
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        conn.cursor.return_value.__exit__.return_value = False

        @contextmanager
        def fake_borrow(_pool):
            yield conn

        with (
            patch.object(pgdata.config, "search_enabled", True),
            patch.object(
                pgdata.config, "search_fts_regconfig_for_brain", return_value="italian"
            ),
            patch.object(pgdata, "ensure_brain_database"),
            patch.object(pgdata, "get_brain_pool"),
            patch.object(pgdata, "borrow", fake_borrow),
        ):
            client._ensure_brain_schema("searchbenchitsmoke")
        executed = " ".join(str(call.args[0]) for call in cur.execute.call_args_list)
        self.assertIn("search_tsv_alt", executed)
        self.assertIn("to_tsvector('italian'", executed)
        self.assertNotIn("to_tsvector('english'", executed)
        self.assertIn("searchbenchitsmoke", client._search_alt_brains)


if __name__ == "__main__":
    unittest.main()
