import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

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
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USERNAME": "postgres",
    "POSTGRES_PASSWORD": "postgres",
}
for key, value in ENV_DEFAULTS.items():
    os.environ.setdefault(key, value)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.dates import (
    normalize_date_string,
    parse_date_string,
    resolve_relative_date,
    to_naive_utc,
)

LOCOMO_TIMESTAMP = "1:56 pm on 8 May, 2023"


class ParseDateStringTests(unittest.TestCase):
    def test_locomo_session_timestamp_parses(self):
        self.assertEqual(
            parse_date_string(LOCOMO_TIMESTAMP),
            datetime(2023, 5, 8, 13, 56),
        )

    def test_locomo_morning_timestamp_parses(self):
        self.assertEqual(
            parse_date_string("10:37 am on 27 June, 2023"),
            datetime(2023, 6, 27, 10, 37),
        )

    def test_iso_variants_parse(self):
        expected = datetime(2023, 5, 8, 13, 56)
        for value in (
            "2023-05-08T13:56:00",
            "2023-05-08T13:56:00.000000",
            "2023-05-08T13:56:00Z",
            "2023-05-08T13:56:00+00:00",
            "2023-05-08 13:56:00",
            "2023-05-08 13:56:00.000000",
            "2023-05-08 13:56:00+00:00",
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_date_string(value), expected)

    def test_offset_aware_input_is_converted_to_naive_utc(self):
        parsed = parse_date_string("2023-05-08T13:56:00+02:00")
        self.assertEqual(parsed, datetime(2023, 5, 8, 11, 56))
        self.assertIsNone(parsed.tzinfo)

    def test_parsed_value_never_breaks_subtraction(self):
        parsed = parse_date_string("2023-05-08T13:56:00-05:00")
        self.assertIsInstance(datetime.now() - parsed, timedelta)

    def test_ambiguous_numeric_date_is_day_first(self):
        self.assertEqual(parse_date_string("08/05/2023"), datetime(2023, 5, 8))
        self.assertEqual(normalize_date_string("08/05/2023"), "08/05/2023")

    def test_day_first_holds_for_unambiguous_high_day(self):
        self.assertEqual(parse_date_string("25/05/2023"), datetime(2023, 5, 25))

    def test_unparseable_value_returns_none(self):
        self.assertIsNone(parse_date_string("sometime last summer"))

    def test_previously_supported_formats_still_parse(self):
        expected = datetime(2023, 5, 8)
        for value in ("08/05/2023", "2023-05-08", "2023/05/08", "08-05-2023",
                      "May 8, 2023", "May 8 2023", "8 May 2023"):
            with self.subTest(value=value):
                self.assertEqual(parse_date_string(value), expected)


class NormalizeDateStringTests(unittest.TestCase):
    def test_output_format_is_day_first_slashed(self):
        self.assertEqual(normalize_date_string(LOCOMO_TIMESTAMP), "08/05/2023")
        self.assertEqual(normalize_date_string("2023-05-08T13:56:00Z"), "08/05/2023")
        self.assertEqual(normalize_date_string("2023-05-08"), "08/05/2023")

    def test_unparseable_value_passes_through_stripped(self):
        self.assertEqual(
            normalize_date_string("  sometime last summer  "),
            "sometime last summer",
        )


class ResolveRelativeDateTests(unittest.TestCase):
    def test_yesterday_against_locomo_timestamp(self):
        self.assertEqual(
            resolve_relative_date("yesterday", LOCOMO_TIMESTAMP),
            "07/05/2023",
        )

    def test_last_tuesday_against_locomo_timestamp(self):
        self.assertEqual(
            resolve_relative_date("last Tuesday", LOCOMO_TIMESTAMP),
            "02/05/2023",
        )

    def test_days_ago_against_locomo_timestamp(self):
        self.assertEqual(
            resolve_relative_date("3 days ago", LOCOMO_TIMESTAMP),
            "05/05/2023",
        )

    def test_weeks_ago_against_locomo_timestamp(self):
        self.assertEqual(
            resolve_relative_date("2 weeks ago", LOCOMO_TIMESTAMP),
            "24/04/2023",
        )

    def test_unparseable_expression_returns_input_unchanged(self):
        self.assertEqual(
            resolve_relative_date("a few months ago", LOCOMO_TIMESTAMP),
            "a few months ago",
        )

    def test_unparseable_reference_returns_input_unchanged(self):
        self.assertEqual(
            resolve_relative_date("yesterday", "whenever it was"),
            "yesterday",
        )

    def test_next_month_on_century_non_leap_year_does_not_crash(self):
        self.assertEqual(
            resolve_relative_date("next month", "2100-01-31"),
            "28/02/2100",
        )

    def test_last_month_on_century_non_leap_year_does_not_crash(self):
        self.assertEqual(
            resolve_relative_date("last month", "2100-03-31"),
            "28/02/2100",
        )


class ToNaiveUtcTests(unittest.TestCase):
    def test_naive_value_is_returned_unchanged(self):
        value = datetime(2023, 5, 8, 13, 56)
        self.assertIs(to_naive_utc(value), value)

    def test_aware_value_is_shifted_to_utc(self):
        value = datetime(2023, 5, 8, 13, 56, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(to_naive_utc(value), datetime(2023, 5, 8, 11, 56))


if __name__ == "__main__":
    unittest.main()
