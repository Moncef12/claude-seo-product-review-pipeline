import unittest

from review_pipeline.stages.render import (
    _step,
    document_description,
    html_document,
    pipeline_trace,
    render_body,
)


class RenderTests(unittest.TestCase):
    def test_description_is_emitted_in_html_head(self):
        markdown = "# Product Review\n\n**Meta description:** A precise description.\n"
        description = document_description(markdown)
        rendered = html_document("Product Review", description, "<h1>Review</h1>")
        self.assertIn(
            '<meta name="description" content="A precise description.">',
            rendered,
        )

    def test_pros_and_cons_labels_render_following_bullets_as_lists(self):
        rendered = render_body("**Pros**\n- Clear image\n- Good stand\n\n**Cons**\n- No speakers\n")
        self.assertIn("<p><strong>Pros</strong></p>", rendered)
        self.assertIn("<p><strong>Cons</strong></p>", rendered)
        self.assertEqual(rendered.count("<ul>"), 2)
        self.assertIn("<li>Clear image</li>", rendered)
        self.assertIn("<li>No speakers</li>", rendered)

    def test_pros_and_cons_in_fenced_code_are_unchanged(self):
        rendered = render_body("```markdown\n**Pros**\n- example\n```")
        self.assertIn("<code class=\"language-markdown\">**Pros**\n- example", rendered)

    def test_raw_candidate_panel_is_escaped_and_collapsed(self):
        rendered = html_document(
            "Product Review",
            "Description",
            "<h1>Final</h1>",
            "# Raw <script>alert(1)</script>",
        )
        self.assertIn("Initial Sonnet candidate (raw Markdown)", rendered)
        self.assertNotIn('<details class="raw-draft" open', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>alert(1)</script>", rendered)

    def test_pipeline_trace_has_numbered_collapsed_steps_before_review(self):
        trace = pipeline_trace()
        rendered = html_document(
            "Product Review",
            "Description",
            "<h1>Final review</h1>",
            pipeline_html=trace,
        )
        self.assertEqual(trace.count('class="pipeline-step"'), 12)
        self.assertEqual(trace.count('class="pipeline-number"'), 12)
        self.assertNotIn('<details class="pipeline-step" open', trace)
        self.assertLess(rendered.index("Pipeline trace"), rendered.index("Final review"))
        for number in range(1, 13):
            self.assertIn(f'pipeline-number">{number}</span>', trace)
        for label in (
            "Input: DataForSEO request",
            "System prompt",
            "User prompt and selected source input",
            "Raw Haiku output",
            "Raw Sonnet output",
            "Validation rules",
            "Parsed factual audit",
            "Raw repair output",
            "Final Python validation",
            "Final Haiku factual audit",
            "Output: cleanup report",
            "Renderer configuration",
        ):
            self.assertIn(label, trace)
        self.assertIn("<strong>Next:</strong>", trace)
        self.assertIn("Production summary", trace)
        self.assertIn("Conditional repair/final gate", trace)

    def test_pipeline_artifacts_are_escaped_and_missing_is_available(self):
        step = _step(1, "<Discovery>", [("payload <x>", {"html": "<script>alert(1)</script>"})])
        self.assertIn("&lt;Discovery&gt;", step)
        self.assertIn("payload &lt;x&gt;", step)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", step)
        self.assertNotIn("<script>alert(1)</script>", step)
        missing = _step(2, "Missing", [("none", None)])
        self.assertIn("UNAVAILABLE", missing)
        self.assertIn("Unavailable", missing)

    def test_human_result_label_and_outcome_are_escaped(self):
        step = _step(
            8,
            "Repair",
            [("record", {})],
            result_label="SKIPPED <safe>",
            outcome="No repair <script>alert(1)</script>",
            result_state="passed",
        )
        self.assertIn("SKIPPED &lt;safe&gt;", step)
        self.assertIn("No repair &lt;script&gt;", step)
        self.assertNotIn("<script>alert(1)</script>", step)

    def test_visible_summary_card_is_escaped_and_precedes_collapsed_trace(self):
        summary = {
            "total_calls": 7,
            "calls": {"anthropic": 6, "dataforseo": 1},
            "tokens": {"total": 98652, "input": 75643, "output": 23009},
            "estimated_total_usd": 0.012345,
            "validation": {
                "final_python": "PASS",
                "final_haiku": "PASS",
                "final_haiku_supported": 56,
                "final_haiku_audited": 56,
            },
            "repair": {"status": "skipped <safe>"},
            "initial_failures": [
                {"validator": "Python", "code": "h1", "message": "Fix <title>"}
            ],
        }
        rendered = html_document("Product", "Description", "<p>Review</p>", pipeline_html=pipeline_trace(), production_summary=summary)
        self.assertIn('class="production-summary"', rendered)
        self.assertIn("7 total · Anthropic 6 · DataForSEO 1", rendered)
        self.assertIn("98.7K total · 75.6K in · 23.0K out", rendered)
        self.assertIn("$0.01", rendered)
        self.assertIn("Validation results", rendered)
        self.assertIn("Python rules", rendered)
        self.assertIn("Final article passed deterministic structure, policy, provenance, and decision checks. 1 initial issue repaired.", rendered)
        self.assertIn("Haiku claim audit", rendered)
        self.assertIn("56/56 final claims supported by normalized evidence.", rendered)
        self.assertLess(rendered.index('class="production-summary"'), rendered.index('class="pipeline-trace"'))
        self.assertIn("Initial validation failures (repaired)", rendered)
        self.assertIn("Fix &lt;title&gt;", rendered)

    def test_failure_summary_appears_before_full_validation_artifact(self):
        trace = pipeline_trace()
        python_step = trace.index("Python validation")
        failure_block = trace.index("Initial validation failures (repaired)", python_step)
        validation_rules = trace.index("Validation rules", python_step)
        self.assertLess(failure_block, validation_rules)
        self.assertIn("Python · h1:", trace[failure_block:validation_rules])
        self.assertIn("Python · buyer fit guidance:", trace[failure_block:validation_rules])
        self.assertIn("Python · final conversion cue:", trace[failure_block:validation_rules])


if __name__ == "__main__":
    unittest.main()
