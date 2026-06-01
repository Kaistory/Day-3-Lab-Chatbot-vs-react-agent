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
        # Mặc định gemini-3.1-flash-lite (nhanh, rẻ). DEFAULT_MODEL có thể override.
        model = env_model if env_model.startswith("gemini") else "gemini-3.1-flash-lite"
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
        # GPU offload: 0 = CPU only, -1 = all layers, N>0 = N layers on GPU.
        # Needs a CUDA/Metal/Vulkan build of llama-cpp-python to take effect.
        try:
            n_gpu_layers = int(os.getenv("LOCAL_GPU_LAYERS", "0"))
        except ValueError:
            n_gpu_layers = 0
        # max_tokens: yếu tố chính quyết định độ trễ trên CPU. Mặc định 256.
        try:
            max_tokens = int(os.getenv("LOCAL_MAX_TOKENS", "256"))
        except ValueError:
            max_tokens = 256
        return LocalProvider(
            model_path=default_path,
            n_gpu_layers=n_gpu_layers,
            max_tokens=max_tokens,
        )

    raise ValueError(f"Provider không hỗ trợ: '{provider}'. Dùng: openai | google | local")
