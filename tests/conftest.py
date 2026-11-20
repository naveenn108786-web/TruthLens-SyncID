"""Test setup: every test session uses a fresh temporary database, registry and case folder.
The environment variables must be set before truthlens modules are imported."""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="truthlens-test-")
os.environ["TRUTHLENS_DB"] = os.path.join(_TMP, "truthlens.db")
os.environ["TRUTHLENS_REGISTRY"] = os.path.join(_TMP, "registry")
os.environ["TRUTHLENS_DATA"] = os.path.join(_TMP, "cases")
os.environ["TRUTHLENS_OFFLINE"] = "1"          # never call the LLM from tests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "samples", "demo")


def demo_bytes(name):
    with open(os.path.join(DEMO, name), "rb") as f:
        return f.read()
