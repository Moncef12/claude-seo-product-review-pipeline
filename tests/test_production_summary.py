import importlib
import unittest

from review_pipeline.cli import COLLECT_STAGES, GENERATE_STAGES
from review_pipeline.production_summary import build_production_summary


def usage(input_tokens, output_tokens):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


class ProductionSummaryTests(unittest.TestCase):
    def artifacts(self, repaired=False):
        initial_audit = {"passed": not repaired, "audited_claim_count": 10, "supported_claim_count": 10 if not repaired else 8, "issues": [] if not repaired else [{"category": "unsupported_claim"}, {"category": "contradiction"}]}
        final_audit = {"passed": True, "audited_claim_count": 11, "supported_claim_count": 11, "issues": []}
        return {
            "discovery": {"collection": {"tasks": [{"cost": 0.11}, {"cost": 0.12}], "call_count": 2}},
            "authority": {"cost": 0.13, "call_count": 1},
            "scrape": {"fetch_count": 5},
            "extraction": {"model": "claude-haiku-4-5-20251001", "usage": usage(100, 200)},
            "plan": {"model": "claude-haiku-4-5-20251001", "usage": usage(300, 400)},
            "generation": {"model": "claude-sonnet-4-6", "usage": usage(500, 600)},
            "validation": {"initial": {"passed": not repaired, "issues": []}, "final": {"passed": True, "issues": []}},
            "factual_audit": {"initial": {"usage": usage(700, 800), "audit": initial_audit}, "final": {"usage": usage(1100, 1200), "audit": final_audit} if repaired else None, "final_reused_initial": not repaired},
            "repair": {"repair_called": repaired, "usage": usage(900, 1000) if repaired else usage(0, 0), "word_count": 1000},
            "final_article": "word " * 1000,
        }

    def test_reused_initial_audit_is_counted_once_and_cost_is_transparent(self):
        summary = build_production_summary(artifacts=self.artifacts())
        self.assertEqual(summary["calls"]["dataforseo"], 3)
        self.assertEqual(summary["calls"]["anthropic"], 4)
        self.assertEqual(summary["source_fetch_count"], 5)
        self.assertEqual(summary["total_calls"], 7)
        self.assertEqual(summary["total_api_calls"], 7)
        self.assertEqual(summary["total_external_operations"], 12)
        self.assertEqual(summary["tokens"], {"input": 1600, "output": 2000, "total": 3600})
        self.assertGreater(summary["estimated_total_usd"], 0)
        self.assertIn("effective_date", summary["cost"]["pricing_basis"])
        self.assertIn("exclude hosting", summary["exclusions"].casefold())

    def test_repair_path_counts_repair_and_final_audit(self):
        summary = build_production_summary(artifacts=self.artifacts(repaired=True))
        # Discovery 2 + authority 1 + source fetches 5 + four initial Claude
        # calls + repair + final re-audit.
        self.assertEqual(summary["calls"]["anthropic"], 6)
        self.assertEqual(summary["total_calls"], 9)
        self.assertEqual(summary["total_external_operations"], 14)
        self.assertTrue(summary["repair_required"])
        self.assertTrue(summary["repair_called"])
        self.assertEqual(summary["validation"]["final_haiku_audited"], 11)

    def test_cached_producer_artifacts_retain_calls_and_costs(self):
        artifacts = self.artifacts()
        for stage in ("extraction", "plan", "generation"):
            artifacts[stage]["cached"] = True
            artifacts[stage]["call_count"] = 0
            artifacts[stage]["last_run_cache_hit"] = True
        artifacts["factual_audit"]["initial"]["cached"] = True
        artifacts["factual_audit"]["initial"]["call_count"] = 0
        artifacts["factual_audit"]["initial"]["last_run_cache_hit"] = True
        summary = build_production_summary(artifacts=artifacts)
        self.assertEqual(summary["calls"]["anthropic"], 4)
        self.assertEqual(summary["tokens"], {"input": 1600, "output": 2000, "total": 3600})
        self.assertGreater(summary["estimated_claude_cost_usd"], 0)

    def test_final_word_count_prefers_deterministic_validation(self):
        artifacts = self.artifacts()
        artifacts["validation"]["final"]["word_count"] = 997
        artifacts["repair"]["word_count"] = 1001
        artifacts["final_article"] = "word " * 1003
        summary = build_production_summary(artifacts=artifacts)
        self.assertEqual(summary["final_word_count"], 997)

    def test_shared_authority_cache_hit_adds_no_provider_call(self):
        artifacts = self.artifacts()
        artifacts["authority"] = {
            "targets": ["example.test"],
            "call_count": 0,
            "called": False,
            "cost": 0,
            "effective_scores": {"example.test": 80},
        }
        summary = build_production_summary(artifacts=artifacts)
        self.assertEqual(summary["calls_by_provider_stage"]["DataForSEO"]["authority_bulk_ranks"], 0)
        self.assertEqual(summary["calls"]["dataforseo"], 2)

    def test_cli_stage_modules_import_without_provider_calls(self):
        for module_name in (*COLLECT_STAGES, *GENERATE_STAGES):
            with self.subTest(module=module_name):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
