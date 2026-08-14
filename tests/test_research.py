import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investor_agent.research import OpenAIResearcher  # noqa: E402
from investor_agent.research_sources import public_source_catalog  # noqa: E402


class ResearchResponseTests(unittest.TestCase):
    def test_extracts_answer_citations_sources_and_queries(self):
        response = {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "type": "search",
                        "query": "Visa investor relations results",
                        "sources": [
                            {
                                "url": "https://investor.visa.com/results",
                                "title": "Visa results",
                            }
                        ],
                    },
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Visa publicó resultados recientes.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://investor.visa.com/results",
                                    "title": "Visa results",
                                    "start_index": 0,
                                    "end_index": 4,
                                }
                            ],
                        }
                    ],
                },
            ]
        }

        answer, citations = OpenAIResearcher._answer_and_citations(response)
        sources, queries = OpenAIResearcher._search_metadata(response, citations)

        self.assertEqual("Visa publicó resultados recientes.", answer)
        self.assertEqual(0, citations[0]["start_index"])
        self.assertEqual(1, len(sources))
        self.assertEqual(["Visa investor relations results"], queries)

    def test_rejects_non_http_source_urls(self):
        self.assertIsNone(OpenAIResearcher._safe_url("javascript:alert(1)"))
        self.assertEqual(
            "https://example.com", OpenAIResearcher._safe_url("https://example.com")
        )

    def test_prompt_uses_native_citations_without_manual_urls(self):
        instructions = OpenAIResearcher._instructions("asset", {})

        self.assertIn("citas nativas", instructions)
        self.assertIn("No agregues manualmente una bibliografía", instructions)

    def test_optional_advice_has_an_explicit_section_and_labels(self):
        neutral = OpenAIResearcher._instructions("asset", {})
        with_advice = OpenAIResearcher._instructions(
            "asset", {}, include_advice=True
        )

        self.assertNotIn("## Opinión orientativa", neutral)
        self.assertNotIn("## Zonas de precio de referencia", neutral)
        self.assertIn("## Opinión orientativa", with_advice)
        self.assertIn("## Zonas de precio de referencia", with_advice)
        self.assertIn("Comprar, Mantener, Reducir, Vender", with_advice)
        self.assertIn("No calculable con datos suficientes", with_advice)
        self.assertIn("zona de compra o entrada", with_advice)

    def test_prompt_prioritizes_free_primary_and_market_sources(self):
        instructions = OpenAIResearcher._instructions(
            "asset", {}, include_advice=True
        )

        self.assertIn("SEC EDGAR", instructions)
        self.assertIn("U.S. Treasury", instructions)
        self.assertIn("Twelve Data", instructions)
        self.assertIn("precio de mercado fechado", instructions)

    def test_public_source_catalog_distinguishes_web_and_optional_apis(self):
        sources = public_source_catalog()

        self.assertGreaterEqual(len(sources), 8)
        self.assertTrue(any(item["integration"] == "web" for item in sources))
        self.assertTrue(any(item["integration"] == "optional_api" for item in sources))
        self.assertTrue(any(item["name"] == "SEC EDGAR" for item in sources))

    def test_public_source_catalog_exposes_runtime_configuration(self):
        sources = public_source_catalog(
            {
                "providers": [{"id": "twelve_data", "configured": True}],
                "official_sources": [{"id": "sec_edgar", "configured": False}],
            }
        )
        by_name = {item["name"]: item for item in sources}

        self.assertTrue(by_name["Twelve Data"]["configured"])
        self.assertFalse(by_name["SEC EDGAR"]["configured"])

    def test_incomplete_response_status_is_preserved(self):
        status, reason = OpenAIResearcher._completion_state(
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            }
        )

        self.assertEqual("incomplete", status)
        self.assertEqual("max_output_tokens", reason)


if __name__ == "__main__":
    unittest.main()
