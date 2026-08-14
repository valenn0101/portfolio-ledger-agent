import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investor_agent.ai_parser import OpenAIMovementParser  # noqa: E402


class OpenAIParserConfigTests(unittest.TestCase):
    def test_uses_official_openai_configuration(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-only",
                "OPENAI_MODEL": "gpt-5.6-terra",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
            },
            clear=True,
        ):
            parser = OpenAIMovementParser()

        self.assertTrue(parser.available)
        self.assertEqual("gpt-5.6-terra", parser.model)
        self.assertEqual("https://api.openai.com/v1", parser.base_url)

    def test_accepts_the_previous_variable_without_using_openrouter(self):
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "test-only",
                "OPENROUTER_PARSER_MODEL": "openai/gpt-5.6-terra",
            },
            clear=True,
        ):
            parser = OpenAIMovementParser()

        self.assertTrue(parser.available)
        self.assertEqual("gpt-5.6-terra", parser.model)
        self.assertEqual("https://api.openai.com/v1", parser.base_url)


if __name__ == "__main__":
    unittest.main()
