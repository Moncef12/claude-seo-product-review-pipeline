import unittest

from review_pipeline.factual_validation import (
    audit_prompt,
    buyer_question_requirements,
    discard_self_exonerating_issues,
    editorial_commercial_requirements,
    essential_plan_requirements,
    summarize_audit,
    summarize_claim_checks,
    summarize_buyer_question_checks,
    summarize_decision_checks,
    summarize_plan_checks,
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

PLAN = {
    "primary_intent": "Help readers decide whether this monitor suits travel and gaming.",
    "article_angle": "Balance portability and gaming strengths against power limitations.",
    "editorial_decisions": [
        {
            "decision": "Explain the lack of a built-in battery.",
            "evidence_ids": ["claim-1"],
        }
    ],
    "aio_direct_answer_targets": [
        {
            "question": "Does it have a battery?",
            "answer_direction": "State clearly that it has no built-in battery.",
            "evidence_ids": ["claim-1"],
        }
    ],
    "cro_buyer_objections": [
        {
            "objection": "It needs external power.",
            "response_direction": "Explain the travel tradeoff without minimizing it.",
            "evidence_ids": ["claim-1"],
        }
    ],
    "cta_placement": "Use the last sentence of Final Verdict.",
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
        requirements = essential_plan_requirements(PLAN)
        prompt = audit_prompt("The monitor supports 180Hz.", EVIDENCE, requirements)
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
        self.assertIn("ESSENTIAL SEO/AIO/CRO PLAN REQUIREMENTS", prompt)
        self.assertIn("plan_checks", prompt)
        self.assertIn("EXPLICIT BUYER QUESTIONS TO ANSWER", prompt)
        self.assertIn("buyer_question_checks", prompt)
        self.assertIn("EDITORIAL/COMMERCIAL DECISION REQUIREMENTS", prompt)
        self.assertIn("decision_checks", prompt)

    def test_essential_plan_requirements_select_executable_brief_items(self):
        requirements = essential_plan_requirements(PLAN)
        self.assertEqual([item["id"] for item in requirements], [f"P{i:02d}" for i in range(1, 7)])
        self.assertEqual(requirements[0]["area"], "search_intent")
        self.assertEqual(requirements[-1]["area"], "cro_conversion_cue")

    def test_editorial_commercial_requirements_are_stable_and_evidence_aware(self):
        requirements = editorial_commercial_requirements(PLAN)
        self.assertEqual([item["id"] for item in requirements], [f"D{i:02d}" for i in range(1, 7)])
        self.assertEqual(requirements[0]["area"], "purchase_recommendation")
        self.assertEqual(requirements[-1]["area"], "fit_based_next_step")
        self.assertIn("claim-1", requirements[0]["evidence_ids"])

    def test_all_dynamic_buyer_questions_become_coverage_requirements(self):
        plan = {"buyer_questions": [
            {"question": "How bright is it?", "evidence_ids": ["claim-1"]},
            {"question": "Is it good for editing?", "evidence_ids": ["claim-1"]},
            {"question": "How is it powered?", "evidence_ids": ["claim-1"]},
            {"question": "Is it good for gaming?", "evidence_ids": ["claim-2"]},
        ]}
        requirements = buyer_question_requirements(plan)
        self.assertEqual([item["id"] for item in requirements], ["B01", "B02", "B03", "B04"])
        self.assertEqual(requirements[2]["question"], "How is it powered?")

    def test_combined_audit_fails_when_an_essential_plan_item_is_missing(self):
        article = "The monitor has no battery. Consider it for powered desk use."
        requirements = essential_plan_requirements(
            {
                "primary_intent": "Explain whether it suits powered desk use.",
                "article_angle": "Balance portability against no internal battery.",
            }
        )
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
                }
            ],
            "plan_checks": [
                {
                    "id": "P01",
                    "status": "covered",
                    "article_quote": "Consider it for powered desk use.",
                    "explanation": "The article gives powered-desk buyer guidance.",
                    "suggested_correction": "",
                },
                {
                    "id": "P02",
                    "status": "missing",
                    "article_quote": "",
                    "explanation": "It does not connect portability to the battery limitation.",
                    "suggested_correction": "Explain how external power limits portability.",
                },
            ],
            "decision_checks": [{
                "id": "D01",
                "status": "met",
                "article_quote": "Consider it for powered desk use.",
                "explanation": "The article gives a fit-based decision.",
                "evidence_ids": ["claim-1"],
                "suggested_correction": "",
            }],
        }
        decision_requirements = [{
            "id": "D01", "area": "purchase_recommendation",
            "requirement": "Give a fit-based decision.", "evidence_ids": ["claim-1"],
        }]
        audit = summarize_audit(
            payload,
            EVIDENCE,
            article,
            requirements,
            decision_requirements=decision_requirements,
        )
        self.assertTrue(audit["factual_passed"])
        self.assertFalse(audit["plan_passed"])
        self.assertTrue(audit["decision_passed"])
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["plan_covered_count"], 1)
        self.assertEqual(audit["issues"][0]["category"], "plan_missing")

    def test_combined_audit_blocks_weak_editorial_commercial_decision(self):
        article = "The monitor has no battery."
        requirements = essential_plan_requirements({"primary_intent": "Explain battery limits."})
        decisions = [{
            "id": "D01", "area": "purchase_recommendation",
            "requirement": "Give a clear purchase recommendation.",
            "evidence_ids": ["claim-1"],
        }]
        payload = {
            "claim_checks": [{
                "index": 1, "article_quote": article, "verdict": "supported",
                "severity": "none", "explanation": "Supported by claim-1.",
                "evidence_ids": ["claim-1"], "suggested_correction": "",
            }],
            "plan_checks": [{
                "id": "P01", "status": "covered", "article_quote": article,
                "explanation": "The limitation is explained.", "suggested_correction": "",
            }],
            "decision_checks": [{
                "id": "D01", "status": "missing", "article_quote": "",
                "explanation": "The article gives no purchase decision.",
                "evidence_ids": ["claim-1"],
                "suggested_correction": "Add a conditional recommendation.",
            }],
        }
        audit = summarize_audit(
            payload,
            EVIDENCE,
            article,
            requirements,
            decision_requirements=decisions,
        )
        self.assertTrue(audit["factual_passed"])
        self.assertTrue(audit["plan_passed"])
        self.assertFalse(audit["decision_passed"])
        self.assertFalse(audit["passed"])
        self.assertEqual(audit["decision_met_count"], 0)
        self.assertEqual(audit["issues"][0]["category"], "decision_missing")

    def test_unanswered_buyer_question_blocks_combined_audit(self):
        requirements = [{
            "id": "B01", "area": "buyer_question",
            "question": "How is it powered?", "evidence_ids": ["claim-1"],
        }]
        payload = {"buyer_question_checks": [{
            "id": "B01", "status": "missing", "article_quote": "",
            "explanation": "The article does not explain the power requirement.",
            "suggested_correction": "Explain that it needs external power.",
        }]}
        result = summarize_buyer_question_checks(
            payload,
            requirements,
            "The monitor is portable.",
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["covered_count"], 0)
        self.assertEqual(result["issues"][0]["category"], "buyer_question_missing")

    def test_decision_checks_reject_unknown_evidence_ids(self):
        requirements = [{
            "id": "D01", "area": "purchase_recommendation",
            "requirement": "Give a purchase decision.", "evidence_ids": ["claim-1"],
        }]
        payload = {"decision_checks": [{
            "id": "D01", "status": "met", "article_quote": "Choose it.",
            "explanation": "A decision is present.", "evidence_ids": ["unknown"],
            "suggested_correction": "",
        }]}
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            summarize_decision_checks(payload, requirements, EVIDENCE, "Choose it.")

    def test_plan_checks_require_exact_ids_but_record_paraphrased_quotes(self):
        requirements = essential_plan_requirements({"primary_intent": "Explain buyer fit."})
        bad_id = {
            "plan_checks": [{
                "id": "P02", "status": "covered", "article_quote": "Buyer fit",
                "explanation": "Covered.", "suggested_correction": "",
            }]
        }
        with self.assertRaisesRegex(ValueError, "IDs"):
            summarize_plan_checks(bad_id, requirements, "Buyer fit")
        bad_quote = {
            "plan_checks": [{
                "id": "P01", "status": "covered", "article_quote": "Not in article",
                "explanation": "Covered.", "suggested_correction": "",
            }]
        }
        result = summarize_plan_checks(bad_quote, requirements, "Buyer fit")
        self.assertTrue(result["passed"])
        self.assertFalse(result["checks"][0]["quote_verified"])

    def test_covered_plan_check_discards_optional_correction(self):
        requirements = essential_plan_requirements({"primary_intent": "Explain buyer fit."})
        payload = {
            "plan_checks": [{
                "id": "P01", "status": "covered", "article_quote": "Buyer fit",
                "explanation": "The article covers buyer fit.",
                "suggested_correction": "Optionally add another example.",
            }]
        }
        result = summarize_plan_checks(payload, requirements, "Buyer fit")
        self.assertTrue(result["passed"])
        self.assertEqual(result["checks"][0]["suggested_correction"], "")
        self.assertIn("discarded_optional_correction", result["checks"][0])

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

        factual["issues"].append(
            {"category": "plan_missing", "explanation": "Add buyer objection coverage"}
        )
        issues = repair_issues(mechanical, factual)
        self.assertEqual(issues[2]["validator"], "haiku_plan_audit")

        factual["issues"].append(
            {"category": "decision_missing", "explanation": "Add a recommendation"}
        )
        issues = repair_issues(mechanical, factual)
        self.assertEqual(issues[3]["validator"], "haiku_editorial_commercial_audit")

        factual["issues"].append(
            {"category": "buyer_question_missing", "explanation": "Answer power needs"}
        )
        issues = repair_issues(mechanical, factual)
        self.assertEqual(issues[4]["validator"], "haiku_buyer_question_audit")

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
