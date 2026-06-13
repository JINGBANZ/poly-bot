"""Strategy plugins for the event-driven core.

build_default_strategies() returns the production set; each strategy keeps
its legacy semantics (entry rules, guardrails, cadence) — what changed is
the runtime: each runs on its own actor with wall-clock timers, and none of
them can ever delay the risk hot path.
"""

from .. import config
from ..core.strategy import FunctionStrategy, StrategyPlugin


def build_default_strategies() -> list[StrategyPlugin]:
    strategies: list[StrategyPlugin] = []

    # Tipoff 90 — NBA pre-game heavy favorites (timer-driven Gamma scan;
    # entries hold to resolution, so it claims its positions via owns_fn).
    from ..tipoff90 import is_tipoff90_position, run_tipoff90_check
    strategies.append(FunctionStrategy(
        "TIPOFF90", run_tipoff90_check,
        interval_sec=config.LOOP_INTERVAL_SEC,
        owns_fn=is_tipoff90_position))

    # Crypto threshold crossings — fast path, no LLM.
    from ..threshold_monitor import run_threshold_check
    strategies.append(FunctionStrategy(
        "THRESHOLD", run_threshold_check,
        interval_sec=config.LOOP_INTERVAL_SEC))

    # Whale detection on held positions.
    from .whale import WhaleStrategy
    strategies.append(WhaleStrategy())

    return strategies
