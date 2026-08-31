import unittest

from review_pipeline.factual_validation import (
    audit_prompt,
    discard_self_exonerating_issues,
    summarize_claim_checks,
    validate_audit,
    visible_text,
    quote_matches_article,
)
from review_pipeline.stages.repair import repair_issues


EVIDENCE = {
    "product": {"brand": "Arzopa", "model": "Z3FC"},
    "claims": [
        {
            "id": "claim-1",
            "claim": "The monitor has no built-in battery.",
            "status": "manufacturer_claim",
            "sources": ["Example"],
        },
        {
            "id": "claim-2",
            "claim": "The display supports a 180Hz refresh rate via USB-C.",
            "status": "manufacturer_claim",
            "sources": ["Example"],
        },
    ],
    "conflicts": [],
}


class FactualValidationTests(unittest.TestCase):
    def test_clean_audit_passes(self):
        audit = {
            "passed": True,
            "audited_claim_count": 2,
            "supported_claim_count": 2,
            "issues": [],
        }
        self.assertIs(validate_audit(audit, EVIDENCE), audit)

    def test_grounding_issue_with_known_evidence_id_is_valid(self):
        audit = {
            "passed": False,
            "audited_claim_count": 2,
            "supported_claim_count": 1,
            "issues": [
                {
                    "category": "contradiction",
                    "severity": "critical",
                    "article_quote": "The monitor has a built-in battery.",
                    "explanation": "The evidence says it has no built-in battery.",
                    "evidence_ids": ["claim-1"],
                    "suggested_correction": "State that no battery is built in.",
                }
            ],
        }
        self.assertFalse(validate_audit(audit, EVIDENCE)["passed"])

    def test_inconsistent_audit_and_unknown_evidence_ids_fail(self):
        inconsistent = {
            "passed": True,
            "audited_claim_count": 1,
            "supported_claim_count": 0,
            "issues": [
                {
                    "category": "unsupported_claim",
                    "severity": "major",
                    "article_quote": "Bad claim",
                    "explanation": "Unsupported",
                    "evidence_ids": [],
                    "suggested_correction": "Remove it",
                }
            ],
        }
        with self.assertRaises(ValueError):
            validate_audit(inconsistent, EVIDENCE)

        inconsistent["passed"] = False
        inconsistent["supported_claim_count"] = 0
        inconsistent["issues"][0]["evidence_ids"] = ["unknown"]
        with self.assertRaises(ValueError):
            validate_audit(inconsistent, EVIDENCE)

    def test_prompt_requires_exhaustive_evidence_only_audit(self):
        prompt = audit_prompt("The monitor supports 180Hz.", EVIDENCE)
        self.assertIn("Audit every factual assertion", prompt)
        self.assertIn("Do not validate claims from general knowledge", prompt)
        self.assertIn("did not necessarily measure", prompt)
        self.assertIn("Absence-of-evidence qualifiers are not contradictions", prompt)
        self.assertIn("conflicts` as first-class evidence", prompt)
        self.assertIn("Calibration examples", prompt)
        self.assertIn("buyer-fit statements", prompt)
        self.assertIn("commercial call to action", prompt)
        self.assertIn("claim-2", prompt)
        self.assertIn("The monitor supports 180Hz.", prompt)

    def test_self_exonerating_issue_is_discarded(self):
        article = "The manufacturer claims a 9ms response time."
        audit = {
            "passed": False,
            "audited_claim_count": 1,
            "supported_claim_count": 0,
            "issues": [
                {
                    "category": "unverified_number",
                    "severity": "major",
                    "article_quote": article,
                    "explanation": "The article wording is accurate but could add a citation.",
                    "evidence_ids": ["claim-1"],
                    "suggested_correction": "Add a citation.",
                }
            ],
        }
        cleaned = discard_self_exonerating_issues(audit)
        self.assertTrue(cleaned["passed"])
        self.assertEqual(cleaned["issues"], [])
        self.assertEqual(len(cleaned["discarded_self_exonerating_issues"]), 1)
        self.assertTrue(validate_audit(cleaned, EVIDENCE, article)["passed"])

    def test_python_and_haiku_issues_are_combined_for_one_repair(self):
        mechanical = {"issues": [{"code": "h1", "message": "Fix title"}]}
        factual = {
            "issues": [
                {
                    "category": "unsupported_claim",
                    "explanation": "Remove unsupported battery capacity",
                }
            ]
        }
        issues = repair_issues(mechanical, factual)
        self.assertEqual(issues[0]["validator"], "python")
        self.assertEqual(issues[1]["validator"], "haiku_factual_audit")

    def test_visible_quote_matches_markdown_link_text(self):
        article = "PCWorld measured [99% sRGB](https://example.com/review)."
        quote = "PCWorld measured 99% sRGB."
        self.assertIn(visible_text(quote), visible_text(article))

    def test_quote_match_ignores_table_and_punctuation_formatting(self):
        article = "| **Weight** | 1.6–1.72 lb (780g) |"
        self.assertTrue(quote_matches_article("Weight: 1.6-1.72 lb (780g)", article))

    def test_claim_matrix_derives_blocking_audit(self):
        article = "The monitor has no battery. It has a 10,000mAh battery."
        payload = {
            "claim_checks": [
                {
                    "index": 1,
                    "article_quote": "The monitor has no battery.",
                    "verdict": "supported",
                    "severity": "none",
                    "explanation": "Supported by claim-1.",
                    "evidence_ids": ["claim-1"],
                    "suggested_correction": "",
                },
                {
                    "index": 2,
                    "article_quote": "It has a 10,000mAh battery.",
                    "verdict": "contradiction",
                    "severity": "critical",
                    "explanation": "The evidence says no battery is built in.",
                    "evidence_ids": ["claim-1"],
                    "suggested_correction": "Remove the battery-capacity claim.",
                },
            ]
        }
        audit = summarize_claim_checks(payload, EVIDENCE, article)
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["audited_claim_count"], 2)
        self.assertEqual(audit["supported_claim_count"], 1)
        self.assertEqual(audit["issues"][0]["category"], "contradiction")

    def test_claim_matrix_requires_consecutive_indexes(self):
        payload = {
            "claim_checks": [
                {
                    "index": 2,
                    "article_quote": "The monitor has no battery.",
                    "verdict": "supported",
                    "severity": "none",
                    "explanation": "Supported by claim-1.",
                    "evidence_ids": ["claim-1"],
                    "suggested_correction": "",
                }
            ]
        }
        with self.assertRaises(ValueError):
            summarize_claim_checks(payload, EVIDENCE, "The monitor has no battery.")

    def test_supported_paraphrased_quote_is_recorded_but_does_not_block(self):
        payload = {
            "claim_checks": [
                {
                    "index": 1,
                    "article_quote": "No internal battery is present.",
                    "verdict": "supported",
                    "severity": "none",
                    "explanation": "Supported by claim-1.",
                    "evidence_ids": ["claim-1"],
                    "suggested_correction": "",
                }
            ]
        }
        audit = summarize_claim_checks(
            payload,
            EVIDENCE,
            "The monitor has no built-in battery.",
        )
        self.assertTrue(audit["passed"])
        self.assertFalse(audit["claim_checks"][0]["quote_verified"])

    def test_blocking_issue_requires_an_exact_article_quote(self):
        payload = {
            "claim_checks": [
                {
                    "index": 1,
                    "article_quote": "It contains an internal battery.",
                    "verdict": "contradiction",
                    "severity": "critical",
                    "explanation": "The evidence says no battery is built in.",
                    "evidence_ids": ["claim-1"],
                    "suggested_correction": "State that there is no built-in battery.",
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "Blocking Haiku"):
            summarize_claim_checks(payload, EVIDENCE, "It has a battery inside.")


if __name__ == "__main__":
    unittest.main()
