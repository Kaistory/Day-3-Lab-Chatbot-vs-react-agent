"""
Smoke test: can llama-cpp load the bundled Phi-3 model on THIS CPU and
generate a few tokens without an illegal-instruction crash (0xc000001d)?

Exit code 0 = works. A hard crash kills the process with a non-zero code.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

MODEL = os.getenv("LOCAL_MODEL_PATH", "./Phi-3-mini-4k-instruct-q4.gguf")

print(f"llama-cpp version check + load: {MODEL}", flush=True)
from llama_cpp import Llama

llm = Llama(model_path=MODEL, n_ctx=512, n_threads=4, verbose=False)
out = llm("<|user|>\nSay 'hello' in one word.<|end|>\n<|assistant|>", max_tokens=8)
print("GENERATED:", out["choices"][0]["text"].strip(), flush=True)
print("LOCAL_OK", flush=True)
