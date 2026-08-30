import unittest

from review_pipeline.stages.extract import reviews_hash, validate_extraction
from review_pipeline.stages.normalize import normalize


REVIEWS = [
    {
        "publisher": "Source One",
        "url": "https://one.test/review",
        "content_sha256": "a" * 64,
    },
    {
        "publisher": "Source Two",
        "url": "https://two.test/review",
        "content_sha256": "b" * 64,
    },
]


def extraction():
    return {
        "sources": [
            {
                "publisher": review["publisher"],
                "url": review["url"],
                "exact_model_reviewed": True,
                "review_context": "Reviewed with a laptop.",
            }
            for review in REVIEWS
        ],
        "claims": [
            {
                "category": "measurement",
                "claim": "Brightness measured at 300 nits.",
                "status": "measured",
                "sources": ["Source One"],
                "conditions": "Default mode",
            },
            {
                "category": "strength",
                "claim": "Text appears sharp at native resolution.",
                "status": "reviewer_assessment",
                "sources": ["Source One", "Source Two"],
                "conditions": None,
            },
        ],
        "conflicts": [
            {
                "topic": "brightness",
                "positions": [
                    {"publisher": "Source One", "claim": "Measured 300 nits."},
                    {"publisher": "Source Two", "claim": "Measured 390 nits."},
                ],
                "editorial_guidance": "Disclose both measurements.",
            }
        ],
    }


class CompactEvidenceTests(unittest.TestCase):
    def test_extraction_requires_exact_sources_and_valid_provenance(self):
        value = extraction()
        validate_extraction(value, REVIEWS)
        value["claims"][0]["sources"] = ["Unknown Publisher"]
        with self.assertRaisesRegex(ValueError, "provenance"):
            validate_extraction(value, REVIEWS)

    def test_normalization_is_compact_and_preserves_conflicts(self):
        output = normalize({"extraction": extraction()})
        self.assertEqual(output["source_count"], 2)
        self.assertTrue(output["all_sources_confirm_exact_model"])
        self.assertEqual(len(output["claims"]), 2)
        self.assertEqual(output["claims"][1]["sources"], ["Source One", "Source Two"])
        self.assertNotIn("url", output["claims"][1])
        self.assertEqual(output["conflicts"][0]["topic"], "brightness")

    def test_review_hash_changes_with_source_content(self):
        first = reviews_hash(REVIEWS)
        changed = [dict(review) for review in REVIEWS]
        changed[0]["content_sha256"] = "c" * 64
        self.assertNotEqual(first, reviews_hash(changed))


if __name__ == "__main__":
    unittest.main()
