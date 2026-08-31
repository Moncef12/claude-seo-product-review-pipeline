import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from review_pipeline.stages.generate import current_generation, generation_prompt, plan_hash
from review_pipeline.stages.plan import (
    MODEL,
    PLAN_PROMPT_VERSION,
    SYSTEM_PROMPT as PLAN_SYSTEM_PROMPT,
    plan_cache_key,
    plan_prompt,
    planning_evidence,
    resolve_plan_evidence_ids,
    planning_input_hash,
    validate_plan,
)
from review_pipeline.stages.extract import SYSTEM_PROMPT as EXTRACTION_SYSTEM_PROMPT, load_reviews
from review_pipeline.stages.repair import repair_prompt


EVIDENCE = {
    "claims": [
        {"id": "claim-screen", "claim": "The display is 180Hz.", "status": "manufacturer_claim"},
        {"id": "claim-port", "claim": "Mini HDMI carries video and audio.", "status": "observed"},
    ]
}


def valid_plan():
    return {
        "primary_intent": "Decide whether this portable monitor fits a buyer's setup.",
        "secondary_intents": ["gaming", "travel"],
        "target_reader": "Laptop and console buyers comparing portable displays.",
        "funnel_stage": "commercial investigation",
        "recurring_serp_topics": [{"topic": "refresh rate", "serp_basis": ["organic titles"]}],
        "buyer_questions": [{"question": "Does it support gaming?", "evidence_ids": ["claim-screen"], "serp_basis": ["PAA"]}],
        "content_gaps": [{"gap": "Explain the practical trade-off of high refresh rate and portability.", "evidence_ids": ["claim-screen"], "serp_basis": ["related searches"]}],
        "article_angle": "A conditional buyer decision built around portability and evidence.",
        "editorial_decisions": [{"decision": "Lead with the use-case trade-off.", "evidence_ids": ["claim-screen"] , "serp_basis": []}],
        "aio_direct_answer_targets": [{"question": "Is it suitable for gaming?", "answer_direction": "Answer conditionally from the refresh-rate evidence.", "evidence_ids": ["claim-screen"], "serp_basis": ["PAA"]}],
        "cro_buyer_objections": [{"objection": "Will connectivity limit use?", "response_direction": "Explain the mini HDMI capability and conditions.", "evidence_ids": ["claim-port"], "serp_basis": []}],
        "cta_placement": "Use a next-step cue in Final Verdict after the conditional recommendation.",
        "ordered_outline": [{"heading": "Quick Verdict", "purpose": "Give the decision first.", "evidence_ids": ["claim-screen"], "serp_basis": []}],
    }


class PlanningTests(unittest.TestCase):
    def test_evidence_ids_are_required_and_must_be_known(self):
        plan = valid_plan()
        self.assertIs(validate_plan(plan, EVIDENCE), plan)
        plan["content_gaps"][0]["evidence_ids"] = []
        plan["content_gaps"][0]["serp_basis"] = []
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            validate_plan(plan, EVIDENCE)
        plan = valid_plan()
        plan["content_gaps"][0]["evidence_ids"] = []
        self.assertIs(validate_plan(plan, EVIDENCE), plan)
        plan = valid_plan()
        plan["editorial_decisions"][0]["evidence_ids"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_plan(plan, EVIDENCE)

    def test_planning_cache_hash_includes_serp_and_qualification_inputs(self):
        base = planning_input_hash(EVIDENCE, {"related_searches": ["portable monitor"]}, {"selected": ["a"]}, [["Review"]])
        changed = planning_input_hash(EVIDENCE, {"related_searches": ["gaming monitor"]}, {"selected": ["a"]}, [["Review"]])
        self.assertNotEqual(base, changed)
        self.assertNotEqual(plan_cache_key(base), plan_cache_key(changed))
        self.assertNotEqual(plan_cache_key(base, MODEL, PLAN_PROMPT_VERSION), plan_cache_key(base, MODEL, "next-version"))

    def test_planning_hash_ignores_run_metadata_but_changes_for_semantic_qualification(self):
        qualification = {
            "created_at": "2026-01-01T00:00:00Z",
            "fetch_count": 5,
            "cached_count": 0,
            "selected": [{"url": "https://a.test/review", "content_sha256": "abc", "total_score": 81, "score_breakdown": {"authority": 8}}],
            "considered": [{"url": "https://a.test/review", "cache_hit": False, "authority_rank": 80}],
        }
        changed_timestamp = dict(qualification, created_at="2026-02-01T00:00:00Z", fetch_count=0, cached_count=5)
        self.assertEqual(
            planning_input_hash(EVIDENCE, {}, qualification, [["Review"]]),
            planning_input_hash(EVIDENCE, {}, changed_timestamp, [["Review"]]),
        )
        changed_url = dict(qualification, selected=[{**qualification["selected"][0], "url": "https://b.test/review"}])
        changed_score = dict(qualification, selected=[{**qualification["selected"][0], "total_score": 82}])
        changed_content = dict(qualification, selected=[{**qualification["selected"][0], "content_sha256": "def"}])
        self.assertNotEqual(planning_input_hash(EVIDENCE, {}, qualification, [["Review"]]), planning_input_hash(EVIDENCE, {}, changed_url, [["Review"]]))
        self.assertNotEqual(planning_input_hash(EVIDENCE, {}, qualification, [["Review"]]), planning_input_hash(EVIDENCE, {}, changed_score, [["Review"]]))
        self.assertNotEqual(planning_input_hash(EVIDENCE, {}, qualification, [["Review"]]), planning_input_hash(EVIDENCE, {}, changed_content, [["Review"]]))

    def test_generation_prompt_and_hash_include_plan(self):
        first = {"article_angle": "lead with portability"}
        second = {"article_angle": "lead with gaming"}
        self.assertIn("lead with portability", generation_prompt(EVIDENCE, first))
        self.assertNotEqual(plan_hash(first), plan_hash(second))

    def test_planning_aliases_resolve_to_normalized_evidence_ids(self):
        compact, aliases = planning_evidence(EVIDENCE)
        self.assertEqual([claim["id"] for claim in compact["claims"]], ["E01", "E02"])
        self.assertEqual(aliases["E01"], "claim-screen")
        plan = valid_plan()
        plan["content_gaps"][0]["evidence_ids"] = ["E1"]
        self.assertEqual(
            resolve_plan_evidence_ids(plan, EVIDENCE)["content_gaps"][0]["evidence_ids"],
            ["claim-screen"],
        )

    def test_prompts_keep_product_facts_in_evidence_and_mark_inputs_untrusted(self):
        prompt = generation_prompt(EVIDENCE, {"article_angle": "evidence-first"})
        self.assertNotIn("Weight must remain approximately", prompt)
        self.assertNotIn("Mini HDMI carries audio and video", prompt)
        self.assertNotIn("State that AMD FreeSync is supported", prompt)
        self.assertIn("Evidence overrides any plan wording", prompt)
        self.assertIn("not a source of product facts", prompt)
        repaired_prompt = repair_prompt("# Draft", [], EVIDENCE, {"article_angle": "evidence-first"})
        self.assertIn('one H1 containing "Arzopa", "Z3FC", and "Review"', repaired_prompt)
        self.assertNotIn("# Arzopa Z3FC Review: A 2.5K 180Hz Portable Monitor", repaired_prompt)
        self.assertIn("untrusted content data", PLAN_SYSTEM_PROMPT)
        self.assertIn("untrusted content data", EXTRACTION_SYSTEM_PROMPT)
        self.assertIn("untrusted content", plan_prompt(EVIDENCE, {}, {}, []))

    def test_manifest_cache_provenance_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scraped = root / "scraped"
            scraped.mkdir()
            (scraped / "selected.json").write_text(
                json.dumps({"publisher": "Actual", "url": "https://actual.test/review", "content_sha256": "actual"}),
                encoding="utf-8",
            )
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps([{"cache_file": "selected.json", "publisher": "Expected", "url": "https://actual.test/review", "content_sha256": "actual"}] + [{"cache_file": f"other-{i}.json"} for i in range(4)]), encoding="utf-8")
            with patch("review_pipeline.stages.extract.SCRAPED_REVIEWS_DIR", scraped):
                with self.assertRaisesRegex(ValueError, "manifest publisher"):
                    load_reviews(manifest)

    def test_extraction_reads_only_manifest_cache_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scraped = root / "scraped"
            scraped.mkdir()
            for index in range(5):
                (scraped / f"selected-{index}.json").write_text(json.dumps({"publisher": f"P{index}", "url": f"https://p{index}.test", "content_sha256": str(index)}), encoding="utf-8")
            # A rejected extra cache file must not be included.
            (scraped / "rejected.json").write_text(json.dumps({"publisher": "Rejected"}), encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps([{"cache_file": f"selected-{index}.json"} for index in range(5)]), encoding="utf-8")
            with patch("review_pipeline.stages.extract.SCRAPED_REVIEWS_DIR", scraped):
                reviews = load_reviews(manifest)
            self.assertEqual([review["publisher"] for review in reviews], [f"P{index}" for index in range(5)])


if __name__ == "__main__":
    unittest.main()
