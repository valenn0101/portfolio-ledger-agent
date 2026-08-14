import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investor_agent.config import load_env_file


class EnvConfigTests(unittest.TestCase):
    def test_loads_values_and_preserves_existing_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "# comentario\nTEST_AGENT_KEY=from-file\nTEST_AGENT_NAME=Portfolio Ledger Agent\n",
                encoding="utf-8",
            )
            os.environ["TEST_AGENT_KEY"] = "from-system"
            os.environ.pop("TEST_AGENT_NAME", None)

            load_env_file(env_path)

            self.assertEqual(os.environ["TEST_AGENT_KEY"], "from-system")
            self.assertEqual(os.environ["TEST_AGENT_NAME"], "Portfolio Ledger Agent")

            os.environ.pop("TEST_AGENT_KEY", None)
            os.environ.pop("TEST_AGENT_NAME", None)


if __name__ == "__main__":
    unittest.main()
