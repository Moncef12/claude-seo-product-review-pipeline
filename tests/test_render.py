import unittest

from review_pipeline.stages.render import (
    _artifact_block,
    _audit_brief_block,
    _failure_block,
    _plan_brief_block,
    _qualification_block,
    _step,
    document_description,
    html_document,
    pipeline_trace,
    render_body,
)


class RenderTests(unittest.TestCase):
    def test_plan_brief_is_plain_language_and_escaped(self):
        rendered = _plan_brief_block({"plan": {
            "primary_intent": "Choose <one>", "target_reader": "Buyers", "funnel_stage": "Consideration",
            "article_angle": "Balanced", "cta_placement": "End", "recurring_serp_topics": [{"topic": "Value <x>", "serp_basis": ["basis"]}],
            "buyer_questions": [{"question": "Which?"}], "content_gaps": [{"gap": "Gap"}], "editorial_decisions": [{"decision": "Lead"}],
            "aio_direct_answer_targets": [{"question": "What?", "answer_direction": "Answer"}],
            "cro_buyer_objections": [{"objection": "Concern", "response_direction": "Respond"}],
            "ordered_outline": [{"heading": "Verdict", "purpose": "Decide"}],
        }})
        self.assertIn("Planning brief for the reviewer", rendered)
        self.assertIn("What readers are trying to decide", rendered)
        self.assertIn("&lt;one&gt;", rendered)
        self.assertNotIn("<one>", rendered)
        self.assertIn("Answers to make easy to find", rendered)
        self.assertIn("Planned article order", rendered)
        self.assertNotIn('class="plan-brief-card" open', rendered)

    def test_audit_brief_separates_grounding_and_plan_coverage(self):
        rendered = _audit_brief_block({
            "passed": False, "factual_passed": True, "plan_passed": False, "decision_passed": True,
            "supported_claim_count": 4, "audited_claim_count": 5, "plan_covered_count": 1, "plan_checked_count": 2,
            "decision_met_count": 1, "decision_checked_count": 1,
            "factual_issues": [{"category": "brightness", "quote": "<bad>", "explanation": "Wrong", "correction": "Fix", "evidence_ids": ["E1"]}],
            "plan_checks": [{"area": "AIO", "status": "partially_covered", "recommendation": "Answer directly", "article_quote": "Quote", "evidence_ids": ["E2"], "explanation": "Some", "suggested_correction": "Add more"}],
            "decision_checks": [{"area": "buyer_segmentation", "status": "met", "requirement": "Guide buyers", "article_quote": "Best for travelers", "evidence_ids": ["E3"], "explanation": "Specific fit guidance", "suggested_correction": ""}],
        })
        self.assertIn("checked separately", rendered)
        self.assertIn("Factual grounding · PASS · 4/5", rendered)
        self.assertIn("SEO/AIO/CRO plan coverage · FAIL · 1/2", rendered)
        self.assertIn("Editorial/commercial decision quality · PASS · 1/1", rendered)
        for value in ("Advice", "Status", "Article passage", "Evidence IDs", "Explanation", "Correction", "&lt;bad&gt;"):
            self.assertIn(value, rendered)
        self.assertNotIn("<bad>", rendered)

    def test_audit_brief_missing_data_is_safe(self):
        self.assertEqual(_audit_brief_block(None), "")

    def test_audit_brief_renders_collapsed_buyer_question_coverage_inside_plan_rubric(self):
        rendered = _audit_brief_block({
            "factual_passed": True, "plan_passed": True, "decision_passed": True,
            "buyer_question_passed": False, "buyer_question_covered_count": 1,
            "buyer_question_checked_count": 2,
            "buyer_question_checks": [
                {"question": "Is it quiet?", "recommendation": "Answer with measured noise", "status": "covered",
                 "article_quote": "Quiet enough for bedrooms", "evidence_ids": ["E7"], "explanation": "Direct answer", "correction": ""},
                {"question": "Does it travel well?", "recommendation": "Explain portability", "status": "missing",
                 "article_quote": "", "evidence_ids": [], "explanation": "No portability guidance", "suggested_correction": "Add a portability note"},
            ],
            "plan_checks": [{"area": "search_intent", "status": "covered"}],
        })
        self.assertIn("Buyer-question coverage · FAIL · 1/2", rendered)
        self.assertIn("Is it quiet?", rendered)
        self.assertIn("Does it travel well?", rendered)
        for value in ("Question", "Recommendation", "Article passage", "Evidence IDs", "Explanation", "Correction", "E7"):
            self.assertIn(value, rendered)
        self.assertNotIn('<details class="audit-subsection" open', rendered)
        self.assertEqual(rendered.count('class="audit-rubric"'), 3)

    def test_audit_brief_lists_all_validated_claims_and_humanizes_area(self):
        rendered = _audit_brief_block({
            "factual_passed": True, "plan_passed": True, "decision_passed": True, "claim_checks": [
                {"index": 1, "verdict": "supported", "article_quote": "<claim>", "evidence_ids": ["E1"], "explanation": "Confirmed"},
                {"index": 2, "verdict": "unsupported", "article_quote": "Other", "explanation": "Needs work", "suggested_correction": "Correct it"},
            ], "plan_checks": [{"area": "aio_direct_answer", "status": "covered", "recommendation": "Answer"}],
        })
        self.assertIn("Validated factual claims", rendered)
        self.assertEqual(rendered.count('class="audit-row"'), 3)
        self.assertIn("Claim 1", rendered)
        self.assertIn("&lt;claim&gt;", rendered)
        self.assertIn("Aio Direct Answer", rendered)
        self.assertNotIn("<claim>", rendered)
        self.assertEqual(rendered.count('class="audit-rubric"'), 3)
        self.assertNotIn('class="audit-rubric" open', rendered)

    def test_plan_audit_empty_evidence_and_correction_are_readable(self):
        rendered = _audit_brief_block({"plan_checks": [{
            "area": "search_intent", "status": "covered", "recommendation": "Fit", "explanation": "Covered",
            "evidence_ids": [], "suggested_correction": "",
        }]})
        self.assertIn("Not required for this planning item", rendered)
        self.assertNotIn("<strong>Correction:</strong>", rendered)

    def test_plan_and_audit_briefs_precede_step_artifacts(self):
        step = _step(5, "Plan", [("Raw Haiku output", {"x": 1})], notice_html=_plan_brief_block({"plan": {"primary_intent": "Intent"}}))
        self.assertLess(step.index("Planning brief for the reviewer"), step.index("Raw Haiku output"))
    def test_qualification_block_shows_expandable_scoring_and_cutoff(self):
        rendered = _qualification_block({
            "selected_count": 1,
            "weights": {"exact_model_relevance": 25},
            "considered": [
                {"publisher": "Good <Publisher>", "root_domain": "good.example", "total_score": 88.5,
                 "result": "selected", "score_breakdown": {"exact_model_relevance": {"points": 25, "reason": "exact match"}}, "reasons": ["exact match"]},
                {"publisher": "Rejected", "root_domain": "bad.example", "total_score": 40,
                 "result": "rejected", "hard_rejection_reasons": ["too thin <page>"]},
            ],
        })
        self.assertIn("Source qualification", rendered)
        self.assertIn("Selection cutoff: the lowest selected score was 88.5", rendered)
        self.assertEqual(rendered.count('class="qualification-row"'), 2)
        self.assertIn("Score breakdown", rendered)
        self.assertIn("exact match", rendered)
        self.assertIn("&lt;Publisher&gt;", rendered)
        self.assertNotIn("<Publisher>", rendered)
        self.assertNotIn('class="qualification-row" open', rendered)

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
        self.assertLess(rendered.index('id="panel-review"'), rendered.index('id="panel-pipeline"'))
        for number in range(1, 13):
            self.assertIn(f'pipeline-number">{number}</span>', trace)
        for label in (
            "Input: DataForSEO request",
            "System prompt",
            "User prompt and selected source input",
            "Raw Haiku output",
            "Raw Sonnet output",
            "Validation rules",
            "Parsed factual, plan, buyer-question, and decision audit",
            "Raw repair output",
            "Final Python validation",
            "Final Haiku factual, plan, buyer-question, and decision audit",
            "Output: cleanup report",
            "Renderer configuration",
        ):
            self.assertIn(label, trace)
        self.assertIn("<strong>Next:</strong>", trace)
        self.assertIn("Production summary", trace)
        self.assertIn("Conditional repair/final gate", trace)

    def test_minimal_tab_navigation_has_associated_panels_and_progressive_default(self):
        rendered = html_document("Product", "Description", "<p>Review</p>", pipeline_html=pipeline_trace())
        self.assertIn('<p class="page-title">Product</p>', rendered)
        self.assertNotIn('<header class="page-header">\n      <p class="eyebrow">Evidence-grounded product review</p>\n      <h1>', rendered)
        self.assertIn('position: sticky', rendered)
        self.assertIn('grid-template-columns: repeat(auto-fit, minmax(145px, 1fr))', rendered)
        for name in ("overview", "review", "pipeline"):
            self.assertIn(f'id="tab-{name}"', rendered)
            self.assertIn(f'aria-controls="panel-{name}"', rendered)
            self.assertIn(f'id="panel-{name}"', rendered)
        self.assertIn('id="tab-overview" role="tab" aria-controls="panel-overview" aria-selected="true"', rendered)
        self.assertIn('id="panel-overview" class="view-panel is-active"', rendered)
        self.assertIn('.js-enabled .view-panel:not(.is-active)', rendered)
        self.assertIn('location.hash.slice(1)', rendered)
        self.assertIn('window.addEventListener("hashchange"', rendered)
        self.assertIn('next.focus(); activate(next.dataset.view)', rendered)

    def test_pipeline_steps_close_siblings_without_touching_nested_details(self):
        rendered = html_document("Product", "Description", "<p>Review</p>", pipeline_html=pipeline_trace())
        self.assertIn('trace.querySelectorAll(":scope > .pipeline-step")', rendered)
        self.assertIn('if (other !== step) other.open = false', rendered)
        self.assertIn('step.addEventListener("toggle"', rendered)

    def test_pipeline_artifacts_are_escaped_and_missing_is_available(self):
        step = _step(1, "<Discovery>", [("payload <x>", {"html": "<script>alert(1)</script>"})])
        self.assertIn("&lt;Discovery&gt;", step)
        self.assertIn("payload &lt;x&gt;", step)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", step)
        self.assertNotIn("<script>alert(1)</script>", step)
        missing = _step(2, "Missing", [("none", None)])
        self.assertIn("UNAVAILABLE", missing)
        self.assertIn("Unavailable", missing)

    def test_pipeline_artifacts_are_nested_closed_disclosures(self):
        artifact = _artifact_block('Label <"x">', {'html': '<script>alert(1)</script>'})
        self.assertEqual(artifact.count('<details class="pipeline-artifact">'), 1)
        self.assertIn('<summary>Label &lt;&quot;x&quot;&gt;</summary>', artifact)
        self.assertIn('&quot;&lt;script&gt;alert(1)&lt;/script&gt;&quot;', artifact)
        self.assertNotIn(' open', artifact)
        self.assertIn('<div class="json-preview">', artifact)
        self.assertIn('class="json-container json-object"', artifact)

    def test_pipeline_artifacts_are_grouped_into_input_and_output_blocks(self):
        step = _step(
            1,
            "Stage",
            [
                ("Input: evidence", {"claim": "supported"}),
                ("System prompt", "Be accurate"),
                ("Run metadata", {"tokens": 10}),
                ("Raw model output", '{"passed": true}'),
            ],
        )
        self.assertEqual(step.count('class="pipeline-io-group input"'), 1)
        self.assertEqual(step.count('class="pipeline-io-group output"'), 1)
        self.assertIn('<h3>Input</h3>', step)
        self.assertIn('<h3>Output</h3>', step)
        input_start = step.index('class="pipeline-io-group input"')
        output_start = step.index('class="pipeline-io-group output"')
        self.assertLess(input_start, step.index("Input: evidence"))
        self.assertLess(step.index("System prompt"), output_start)
        self.assertLess(output_start, step.index("Run metadata"))
        self.assertLess(output_start, step.index("Raw model output"))

    def test_json_preview_is_ordered_typed_and_nested_nodes_closed(self):
        artifact = _artifact_block("Input", {
            "primary_intent": "Evaluate travel",
            "secondary_intents": ["Gaming", {"rank": 2}],
            "enabled": True,
            "score": 4.5,
            "missing": None,
        })
        self.assertIn('class="json-container json-object"', artifact)
        self.assertIn('class="json-key">&quot;primary_intent&quot;', artifact)
        self.assertIn('class="json-string">&quot;Evaluate travel&quot;', artifact)
        self.assertIn('class="json-boolean">true</span>', artifact)
        self.assertIn('class="json-number">4.5</span>', artifact)
        self.assertIn('class="json-null">null</span>', artifact)
        self.assertIn('{5 keys}', artifact)
        self.assertIn('[2 items]', artifact)
        self.assertIn('<details class="json-node">', artifact)
        self.assertNotIn('<details class="json-node" open', artifact)
        self.assertLess(artifact.index('primary_intent'), artifact.index('secondary_intents'))

    def test_complete_json_strings_get_preview_but_ordinary_text_keeps_pre(self):
        preview = _artifact_block("JSON", '{"first": [true, null]}')
        self.assertIn('class="json-container json-object"', preview)
        self.assertIn('class="json-node"', preview)
        ordinary = _artifact_block("Text", 'A prompt containing {"partial": true} prose')
        self.assertIn('<pre>A prompt containing {&quot;partial&quot;: true} prose</pre>', ordinary)
        self.assertNotIn('json-container', ordinary)

    def test_json_preview_escapes_keys_and_values(self):
        artifact = _artifact_block('<label>', {'<key>': '<script>alert(1)</script>'})
        self.assertIn('&quot;&lt;key&gt;&quot;', artifact)
        self.assertIn('&quot;&lt;script&gt;alert(1)&lt;/script&gt;&quot;', artifact)
        self.assertNotIn('<script>', artifact)

    def test_pipeline_artifact_details_are_not_targeted_by_step_sibling_selector(self):
        rendered = html_document("Product", "Description", "<p>Review</p>", pipeline_html=pipeline_trace())
        self.assertIn('trace.querySelectorAll(":scope > .pipeline-step")', rendered)
        self.assertNotIn('trace.querySelectorAll(".pipeline-step, .pipeline-artifact")', rendered)
        self.assertIn('.pipeline-artifact > summary', rendered)
        self.assertNotIn('.pipeline-artifact summary::before', rendered)

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
                "final_haiku_factual": "PASS",
                "final_haiku_plan": "PASS",
                "final_haiku_supported": 56,
                "final_haiku_audited": 56,
                "final_plan_covered": 13,
                "final_plan_checked": 13,
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
        self.assertIn("Haiku plan coverage", rendered)
        self.assertIn("Haiku editorial/commercial", rendered)
        self.assertIn("13/13 essential SEO/AIO/CRO requirements covered", rendered)
        self.assertLess(rendered.index('class="production-summary"'), rendered.index('class="pipeline-trace"'))
        production_summary = rendered[
            rendered.index('class="production-summary"'):rendered.index('class="pipeline-trace"')
        ]
        self.assertNotIn("Initial validation failures (repaired)", production_summary)
        self.assertNotIn("Fix &lt;title&gt;", production_summary)

    def test_failure_summary_appears_before_full_validation_artifact(self):
        failures = [
            {"code": "h1", "message": "Fix the H1."},
            {"code": "buyer_fit_guidance", "message": "Clarify buyer fit."},
            {"code": "final_conversion_cue", "message": "Add a final next step."},
        ]
        trace = _step(
            7,
            "Python validation",
            [("Input: candidate", "Draft"), ("Validation rules", {"required": True})],
            notice_html=_failure_block(failures, default_validator="Python"),
        )
        failure_block = trace.index("Initial validation failures (repaired)")
        validation_rules = trace.index("Validation rules")
        self.assertLess(failure_block, validation_rules)
        self.assertIn("Python · h1:", trace[failure_block:validation_rules])
        self.assertIn("Python · buyer fit guidance:", trace[failure_block:validation_rules])
        self.assertIn("Python · final conversion cue:", trace[failure_block:validation_rules])


if __name__ == "__main__":
    unittest.main()
