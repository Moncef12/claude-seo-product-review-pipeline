import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from review_pipeline.stages import discover
from review_pipeline.stages.discover import classify_source, should_run_fallback
from review_pipeline.stages.scrape import (
    QUALIFICATION_WEIGHTS,
    qualify_and_select,
    score_candidate,
)


def record(domain: str, index: int, **overrides):
    value = {
        "publisher": f"Publisher {index}",
        "url": f"https://{domain}/review-{index}",
        "root_domain": domain,
        "source_type": "independent_candidate",
        "contains_exact_model": True,
        "author_name": "Named Reviewer",
        "author_profile_url": f"https://{domain}/author/reviewer",
        "publication_date": "2025-02-01",
        "headings": ["Review", "Display", "Verdict"],
        "accessibility": "fetched",
        "text": ("The reviewer tested and measured the Arzopa Z3FC display. " * 120),
        "useful_word_count": 900,
        "word_count": 900,
        "content_sha256": f"{index:064x}",
        "authority_rank": 80,
    }
    value.update(overrides)
    return value


class SourceQualificationTests(unittest.TestCase):
    def test_weights_and_authority_author_signals_are_inspectable(self):
        scored = score_candidate(record("example.com", 1))
        self.assertEqual(set(scored["breakdown"]), set(QUALIFICATION_WEIGHTS))
        self.assertEqual(sum(QUALIFICATION_WEIGHTS.values()), 100)
        self.assertEqual(scored["breakdown"]["publisher_site_authority"]["points"], 8)
        self.assertEqual(scored["breakdown"]["author_credibility_transparency"]["points"], 8)
        self.assertTrue(any("profile URL" in reason for reason in scored["reasons"]))

    def test_hard_rejections_and_unique_domain_selection(self):
        candidates = [
            record("a.example.test", 1),
            record("a.example.test", 2),
            record("b.example.test", 3),
            record("c.example.test", 4),
            record("d.example.test", 5),
            record("e.example.test", 6),
            record("f.example.test", 7),
            record("official.example.test", 8, source_type="official"),
            record("wrong.example.test", 9, contains_exact_model=False),
            record("thin.example.test", 10, useful_word_count=40, word_count=40, text="short"),
        ]
        considered, selected = qualify_and_select(candidates, selected_count=5)
        self.assertEqual(len(selected), 5)
        self.assertEqual(len({row["root_domain"] for row in selected}), 5)
        by_domain = {row["root_domain"]: row for row in considered}
        self.assertFalse(by_domain["official.example.test"]["eligible"])
        self.assertFalse(by_domain["wrong.example.test"]["eligible"])
        self.assertFalse(by_domain["thin.example.test"]["eligible"])
        self.assertTrue(any("not an independent" in reason for reason in by_domain["official.example.test"]["reasons"]))

    def test_social_sources_are_not_independent(self):
        self.assertNotEqual(classify_source("facebook.com", "https://facebook.com/example"), "independent_candidate")
        self.assertNotEqual(classify_source("reddit.com", "https://reddit.com/r/monitors"), "independent_candidate")

    def test_fallback_is_conditional_on_pool_and_intent_signals(self):
        organic = [
            {"type": "organic", "url": f"https://publisher-{i}.test/review", "domain": f"publisher-{i}.test"}
            for i in range(8)
        ]
        rich = {"data": {"keyword": "primary"}, "result": [{"items": organic + [{"type": "people_also_ask", "items": [{"question": "Does it travel well?"}]}]}]}
        sparse = {"data": {"keyword": "primary"}, "result": [{"items": organic}]}
        self.assertFalse(should_run_fallback([rich]))
        self.assertTrue(should_run_fallback([sparse]))

    def test_authority_request_uses_live_one_hundred_scale_and_preserves_cost(self):
        response = Mock()
        response.json.return_value = {
            "status_code": 20000,
            "cost": 0,
            "tasks": [{"status_code": 20000, "cost": 0.17, "result": []}],
        }
        with patch.object(discover.requests, "post", return_value=response) as post:
            with tempfile.TemporaryDirectory() as directory:
                with patch.object(discover, "AUTHORITY_RAW_PATH", Path(directory) / "authority.json"), patch.object(discover, "AUTHORITY_DB_PATH", Path(directory) / "authority-db.json"):
                    record, called = discover.collect_authority(["example.com"], "test-auth", refresh=True)
        self.assertTrue(called)
        self.assertEqual(record["cost"], 0.17)
        self.assertTrue(discover.DATAFORSEO_BULK_RANKS_ENDPOINT.endswith("/bulk_ranks/live"))
        self.assertEqual(post.call_args.args[0], discover.DATAFORSEO_BULK_RANKS_ENDPOINT)
        self.assertEqual(post.call_args.kwargs["json"], [{"targets": ["example.com"], "rank_scale": "one_hundred"}])

    def test_legacy_thousand_scale_is_normalized_not_clamped_to_100(self):
        payload = {"tasks": [{"result": [{"target": "example.com", "rank": 850}]}]}
        self.assertEqual(discover.authority_rank_map(payload)["example.com"], 85)

    def test_shared_database_reuses_fresh_entries_and_batches_only_stale_domains(self):
        now = datetime.now(timezone.utc)
        response = Mock()
        response.json.return_value = {
            "status_code": 20000,
            "tasks": [{"status_code": 20000, "cost": 0.21, "result": [{"target": "stale.example", "rank": 70}]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "authority-db.json"
            raw_path = root / "authority-raw.json"
            db_path.write_text(json.dumps({
                "version": 1,
                "ttl_days": 90,
                "entries": {
                    "fresh.example": {
                        "score": 91,
                        "fetched_at": (now - timedelta(days=1)).isoformat(),
                        "endpoint": discover.DATAFORSEO_BULK_RANKS_ENDPOINT,
                        "rank_scale": "one_hundred",
                        "provenance": {"provider": "DataForSEO"},
                    },
                    "stale.example": {
                        "score": 33,
                        "fetched_at": (now - timedelta(days=91)).isoformat(),
                        "endpoint": discover.DATAFORSEO_BULK_RANKS_ENDPOINT,
                        "rank_scale": "one_hundred",
                        "provenance": {"provider": "DataForSEO"},
                    },
                },
            }), encoding="utf-8")
            with patch.object(discover, "AUTHORITY_DB_PATH", db_path), patch.object(discover, "AUTHORITY_RAW_PATH", raw_path), patch.object(discover.requests, "post", return_value=response) as post:
                result, called = discover.collect_authority(["fresh.example", "stale.example", "missing.example"], "test-auth", refresh=True)
            self.assertTrue(called)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs["json"][0]["targets"], ["missing.example", "stale.example"])
            self.assertEqual(result["cache_hit_domains"], ["fresh.example"])
            self.assertEqual(result["requested_domains"], ["missing.example", "stale.example"])
            self.assertEqual(result["effective_scores"], {"fresh.example": 91.0, "missing.example": 0.0, "stale.example": 70.0})
            stored = json.loads(db_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["entries"]["stale.example"]["score"], 70.0)
            self.assertEqual(stored["entries"]["stale.example"]["endpoint"], discover.DATAFORSEO_BULK_RANKS_ENDPOINT)
            self.assertEqual(stored["entries"]["stale.example"]["rank_scale"], "one_hundred")
            self.assertTrue(stored["entries"]["stale.example"]["fetched_at"].endswith("+00:00"))
            self.assertEqual(stored["entries"]["stale.example"]["provenance"]["provider"], "DataForSEO")
            self.assertNotIn("missing.example", stored["entries"])

    def test_refresh_does_not_invalidate_fresh_shared_authority_entries(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "authority-db.json"
            raw_path = root / "authority-raw.json"
            db_path.write_text(json.dumps({"entries": {"fresh.example": {
                "score": 88,
                "fetched_at": now.isoformat(),
                "endpoint": discover.DATAFORSEO_BULK_RANKS_ENDPOINT,
                "rank_scale": "one_hundred",
                "provenance": {"provider": "DataForSEO"},
            }}}), encoding="utf-8")
            with patch.object(discover, "AUTHORITY_DB_PATH", db_path), patch.object(discover, "AUTHORITY_RAW_PATH", raw_path), patch.object(discover.requests, "post") as post:
                result, called = discover.collect_authority(["fresh.example"], "test-auth", refresh=True)
            self.assertFalse(called)
            post.assert_not_called()
            self.assertEqual(result["effective_scores"], {"fresh.example": 88.0})
            self.assertTrue(result["refresh_does_not_invalidate_fresh_entries"])

    def test_prior_product_artifact_seeds_shared_database_without_provider_call(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "authority-db.json"
            raw_path = root / "authority-raw.json"
            raw_path.write_text(
                json.dumps(
                    {
                        "collected_at": now.date().isoformat(),
                        "raw_response": {
                            "tasks": [
                                {
                                    "result": [
                                        {"target": "Example.com", "rank": 84}
                                    ]
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(discover, "AUTHORITY_DB_PATH", db_path), patch.object(
                discover, "AUTHORITY_RAW_PATH", raw_path
            ), patch.object(discover.requests, "post") as post:
                result, called = discover.collect_authority(
                    ["example.com"], "test-auth", refresh=True
                )
            self.assertFalse(called)
            post.assert_not_called()
            self.assertEqual(result["database_status"], "seeded_from_product_artifact")
            self.assertEqual(result["effective_scores"], {"example.com": 84.0})
            self.assertEqual(
                json.loads(db_path.read_text(encoding="utf-8"))["entries"]["example.com"]["score"],
                84.0,
            )

    def test_malformed_shared_database_is_ignored_without_secret_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "authority-db.json"
            raw_path = root / "authority-raw.json"
            db_path.write_text("{not valid json", encoding="utf-8")
            response = Mock()
            response.json.return_value = {"status_code": 20000, "cost": 0, "tasks": []}
            with patch.object(discover, "AUTHORITY_DB_PATH", db_path), patch.object(discover, "AUTHORITY_RAW_PATH", raw_path), patch.object(discover.requests, "post", return_value=response):
                result, called = discover.collect_authority(["missing.example"], "secret-auth", refresh=False)
            self.assertTrue(called)
            self.assertEqual(result["database_status"], "malformed")
            self.assertEqual(result["effective_scores"]["missing.example"], 0.0)
            self.assertNotIn("secret-auth", raw_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
