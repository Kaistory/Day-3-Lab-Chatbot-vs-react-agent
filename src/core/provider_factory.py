"""
Provider factory: build an LLMProvider from environment variables.

Reads DEFAULT_PROVIDER (openai | google | local), defaulting to 'local' so the
lab runs fully offline with the bundled Phi-3 GGUF model.
"""
import os
from typing import Optional

from src.core.llm_provider import LLMProvider


def create_provider(provider: Optional[str] = None) -> LLMProvider:
    """
    Create an LLM provider based on env config (or an explicit override).

    Env vars:
      DEFAULT_PROVIDER  openai | google | local   (default: local)
      DEFAULT_MODEL     model name for openai/google
      OPENAI_API_KEY / GEMINI_API_KEY
      LOCAL_MODEL_PATH  path to .gguf file
    """
    provider = (provider or os.getenv("DEFAULT_PROVIDER", "local")).strip().lower()

    # DEFAULT_MODEL only applies to the provider it belongs to; otherwise we fall
    # back to that provider's own default (avoids e.g. asking Gemini for gpt-4o).
    env_model = os.getenv("DEFAULT_MODEL", "")

    if provider == "openai":
        from src.core.openai_provider import OpenAIProvider
        model = env_model if env_model.startswith("gpt") else "gpt-4o"
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Thiếu OPENAI_API_KEY trong .env")
        return OpenAIProvider(model_name=model, api_key=api_key)

    if provider in ("google", "gemini"):
        from src.core.gemini_provider import GeminiProvider
        model = env_model if env_model.startswith("gemini") else "gemini-2.0-flash"
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Thiếu GEMINI_API_KEY trong .env")
        return GeminiProvider(model_name=model, api_key=api_key)

    if provider == "local":
        from src.core.local_provider import LocalProvider
        # Default to the bundled model at the repo root, fall back to ./models/.
        default_path = os.getenv(
            "LOCAL_MODEL_PATH", "./Phi-3-mini-4k-instruct-q4.gguf"
        )
        if not os.path.exists(default_path):
            alt = "./models/Phi-3-mini-4k-instruct-q4.gguf"
            if os.path.exists(alt):
                default_path = alt
        return LocalProvider(model_path=default_path)

    raise ValueError(f"Provider không hỗ trợ: '{provider}'. Dùng: openai | google | local")
