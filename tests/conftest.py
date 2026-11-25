import pytest


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Tests must be deterministic and offline: the real Gemini is switched off (tests mock it when needed)."""
    monkeypatch.setenv("SIAT_USE_LLM", "0")
