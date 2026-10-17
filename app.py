"""Hugging Face Space entry point (also `uv run python app.py` locally)."""

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent / "src"))
load_dotenv()

from bees_order.services.llm import require_key_for_tracing

require_key_for_tracing()  # before any graph runs: no LangSmith key → nothing is sent

import spaces

from bees_order.ui.page import build_app, launch_kwargs


@spaces.GPU(duration=1)
def _zerogpu_noop() -> None:
    """Never called. ZeroGPU hardware (the free HF tier for Gradio Spaces) refuses to start
    unless one function is decorated with @spaces.GPU. This app needs no GPU: all inference
    goes to OpenRouter. Off ZeroGPU the decorator is a no-op. Delete on CPU hardware."""


demo = build_app()

if __name__ == "__main__":
    demo.launch(**launch_kwargs())
