import unittest

from review_pipeline.validation import validate_markdown
from review_pipeline.stages.validate import dynamic_expected_sources


SOURCES = [
    {"publisher": "Example Labs", "url": "https://example.test/review/alpha-monitor/"},
    {"publisher": "Display Journal", "url": "https://display.test/alpha--monitor-review"},
]
PRODUCT = {"brand": "Acme", "model": "Alpha"}
REQUIRED = ["Quick Verdict", "Specifications", "Frequently Asked Questions", "Methodology"]
META = "An Acme Alpha review covering display quality, connectivity, portability, gaming behavior, compromises, and who should choose this compact monitor."


def valid_article(extra: str = "") -> str:
    filler = " ".join(
        "The Alpha monitor balances clear text, dependable connectivity, and straightforward travel use for ordinary desk work and entertainment.".split()
        * 50
    )
    return f"""# Acme Alpha Review: A Practical Portable Monitor

**Meta description:** {META}

## Quick Verdict

The Alpha is an easy recommendation for readers who value a sharp secondary display and simple setup. {filler}

## Specifications

| Feature | Finding |
|---|---|
| Panel | IPS display |
| Resolution | QHD |
| Ports | USB-C and HDMI |

## Frequently Asked Questions

**Does the Alpha support a laptop?**
Yes, its inputs support compatible laptops.

**Is the Alpha easy to carry?**
Yes, the slim chassis fits in a typical bag.

**Who is the Alpha for?**
It suits travel, study, and ordinary gaming.

## Methodology

This editorial synthesis uses the published evidence from Example Labs and Display Journal. No hands-on testing was conducted by this publication. See [Example Labs](https://example.test/review/alpha-monitor/) and [Display Journal](https://display.test/alpha--monitor-review).

{extra}
"""


class ValidationTests(unittest.TestCase):
    def validate(self, article=None, **kwargs):
        return validate_markdown(
            article or valid_article(),
            PRODUCT,
            REQUIRED,
            SOURCES,
            methodology_heading="Methodology",
            **kwargs,
        )

    def test_valid_article_passes_and_report_is_json_shaped(self):
        report = self.validate()
        self.assertTrue(report["passed"], report["issues"])
        self.assertGreaterEqual(report["word_count"], 800)
        self.assertLessEqual(report["word_count"], 1200)
        self.assertIn("checks", report)
        self.assertIsInstance(report["issues"], list)

    def test_word_count_and_meta_fail(self):
        article = "# Acme Alpha Review\n\n**Meta description:** short\n\n## Methodology\n" + "tiny words " * 10
        report = self.validate(article)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("word_count", codes)
        self.assertIn("meta_description", codes)

    def test_h1_tested_and_first_hand_language_fail(self):
        article = valid_article().replace("# Acme Alpha Review:", "# Acme Alpha Review Tested:")
        article += "\nWe personally tested the monitor in our office."
        report = self.validate(article)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("h1_tested", codes)
        self.assertIn("first_hand_testing", codes)

    def test_negative_hands_on_disclaimer_is_not_first_hand_claim(self):
        article = valid_article().replace(
            "## Methodology",
            "## Methodology\n\nThis is not a hands-on test.",
        )
        report = self.validate(article)
        self.assertTrue(report["checks"]["first_hand_testing"]["passed"], report["issues"])

    def test_reader_faq_can_i_use_is_not_first_hand_claim(self):
        report = self.validate(valid_article().replace(
            "**Who is the Alpha for?**",
            "**Can I use the Alpha for photo editing?**",
        ))
        self.assertTrue(report["checks"]["first_hand_testing"]["passed"], report["issues"])

    def test_required_order_duplicate_and_faq_count(self):
        article = valid_article().replace("## Specifications", "## Quick Verdict\n\n## Specifications", 1)
        article = article.replace("**Who is the Alpha for?**", "**Who is the Alpha for?**\n\n**What is the warranty?**")
        report = self.validate(article)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("duplicate_h2", codes)
        self.assertIn("faq_questions", codes)

    def test_links_are_restricted_to_expected_sources(self):
        report = self.validate(valid_article("\nRead [an unrelated source](https://other.test/review)."))
        self.assertIn("article_links", {issue["code"] for issue in report["issues"]})
        self.assertTrue(report["checks"]["article_links"]["unauthorized"])

    def test_currency_and_current_price_claims_fail(self):
        report = self.validate(valid_article("\nIt costs $199 and the current price may change."))
        self.assertIn("currency_price_claims", {issue["code"] for issue in report["issues"]})

    def test_weight_and_non_price_cost_language_are_not_currency_claims(self):
        report = self.validate(
            valid_article(
                "\nIt weighs 1.6 pounds and trades some weight and cost for portability."
            )
        )
        self.assertTrue(report["checks"]["currency_price_claims"]["passed"], report["issues"])

    def test_punctuation_check_ignores_table_separators_and_url_hyphens(self):
        report = self.validate()
        self.assertTrue(report["checks"]["double_hyphen"]["passed"], report["issues"])
        report = self.validate(valid_article("\nThis sentence uses -- as a separator."))
        self.assertIn("double_hyphen", {issue["code"] for issue in report["issues"]})
        report = self.validate(valid_article("\nThis sentence uses an — em dash."))
        self.assertIn("em_dash", {issue["code"] for issue in report["issues"]})

    def test_collapsed_atx_headings_and_closing_hashes_are_parsed(self):
        article = valid_article().replace("## Quick Verdict", "## Quick Verdict ##").replace("\n\n## Specifications", "\n## Specifications")
        report = self.validate(article)
        self.assertTrue(report["checks"]["required_headings"]["passed"], report["issues"])

    def test_optional_editorial_contract_checks_decision_support(self):
        article = valid_article()
        article = article.replace(
            "## Specifications",
            "## Review Snapshot\n\n| Label | Finding |\n|---|---|\n| Best for | Travel readers |\n| Avoid if | You need built-in speakers |\n| Biggest compromise | Limited ports |\n\n## Specifications",
        )
        article += "\n## Who Should Buy It and Who Should Not\n\nReaders should buy it for travel and avoid it if they need a desktop dock.\n\n## Final Verdict\n\nConsider it as your next step if the trade-offs fit.\n"
        report = self.validate(article, editorial_plan={"article_angle": "decision support"})
        self.assertTrue(report["checks"]["quick_verdict_decision"]["passed"])
        self.assertTrue(report["checks"]["review_snapshot_decision_labels"]["passed"])
        self.assertTrue(report["checks"]["buyer_fit_guidance"]["passed"])
        self.assertTrue(report["checks"]["final_conversion_cue"]["passed"])

    def test_quick_verdict_accepts_clear_strong_product_recommendation(self):
        article = valid_article().replace(
            "The Alpha is an easy recommendation for readers who value a sharp secondary display and simple setup.",
            "The Alpha is a strong portable monitor for travelers who value a sharp secondary display and simple setup.",
        )
        report = self.validate(article, editorial_plan={"article_angle": "decision support"})
        self.assertTrue(report["checks"]["quick_verdict_decision"]["passed"], report["issues"])

    def test_dynamic_runtime_sources_fail_closed_when_missing_or_partial(self):
        with self.assertRaisesRegex(ValueError, "exactly 5"):
            dynamic_expected_sources([], {"sources": SOURCES})
        five = [{"publisher": f"Publisher {index}", "url": f"https://p{index}.test/review"} for index in range(5)]
        self.assertEqual(dynamic_expected_sources(five, {}), five)


if __name__ == "__main__":
    unittest.main()
