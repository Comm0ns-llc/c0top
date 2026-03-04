"""
Discord Quality Scoring Bot package.

Keep top-level imports lightweight so tools that only need `tui_auth`
can run without optional bot/runtime dependencies (e.g. discord.py).
"""

from typing import Any

__version__ = "1.0.2"
__all__ = [
    "bot",
    "main",
    "config",
    "db",
    "nlp_analyzer",
    "scoring_engine",
    "calculate_score",
]


def __getattr__(name: str) -> Any:
    if name in {"bot", "main"}:
        from .bot import bot, main

        return {"bot": bot, "main": main}[name]
    if name == "config":
        from .config import config

        return config
    if name == "db":
        from .database import db

        return db
    if name == "nlp_analyzer":
        from .nlp_analyzer import nlp_analyzer

        return nlp_analyzer
    if name in {"scoring_engine", "calculate_score"}:
        from .scoring import scoring_engine, calculate_score

        return {"scoring_engine": scoring_engine, "calculate_score": calculate_score}[name]
    raise AttributeError(f"module 'src' has no attribute {name!r}")
