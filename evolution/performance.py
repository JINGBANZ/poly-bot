"""Performance analysis — trade log metrics, baseline comparison, regime detection."""

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).parent.parent
STATE_DIR = REPO_ROOT / "evolution" / "state"
BASELINE_FILE = STATE_DIR / "performance_baseline.json"
REGIME_FILE = STATE_DIR / "regime.json"
TRADE_LOG = REPO_ROOT / "state" / "trade_log.jsonl"


def analyze_trades(lookback_hours: int = 168) -> dict:
    """Analyze recent trades from trade_log.jsonl.

    Returns metrics: win_rate, avg_pnl, total_trades, by_strategy, etc.
    """
    cutoff = time.time() - (lookback_hours * 3600)
    trades = []

    try:
        if not TRADE_LOG.exists():
            return {"total_trades": 0, "note": "No trade log found"}

        with open(TRADE_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    trade = json.loads(line)
                    ts = trade.get("timestamp", 0)
                    if isinstance(ts, str):
                        # Try to parse ISO format
                        from datetime import datetime
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    if ts >= cutoff:
                        trades.append(trade)
                except (json.JSONDecodeError, ValueError):
                    continue
    except IOError:
        return {"total_trades": 0, "note": "Could not read trade log"}

    if not trades:
        return {"total_trades": 0, "note": "No trades in lookback period"}

    # Compute metrics
    wins = 0
    losses = 0
    total_pnl = 0.0
    by_strategy = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})

    for t in trades:
        pnl = t.get("pnl", t.get("profit", 0))
        strategy = t.get("strategy", t.get("type", "unknown"))

        if pnl is not None:
            total_pnl += float(pnl)
            if float(pnl) > 0:
                wins += 1
            else:
                losses += 1

            by_strategy[strategy]["trades"] += 1
            by_strategy[strategy]["pnl"] += float(pnl)
            if float(pnl) > 0:
                by_strategy[strategy]["wins"] += 1

    total = wins + losses
    win_rate = (wins / total * 100) if total > 0 else 0

    # Per-strategy win rates
    for s in by_strategy:
        st = by_strategy[s]
        st["win_rate"] = (st["wins"] / st["trades"] * 100) if st["trades"] > 0 else 0

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 4),
        "avg_pnl": round(total_pnl / total, 4) if total > 0 else 0,
        "by_strategy": dict(by_strategy),
        "lookback_hours": lookback_hours,
        "analyzed_at": time.time(),
    }


def compare_baseline() -> Optional[dict]:
    """Compare current metrics against the performance baseline.

    Returns comparison dict with 'degraded' flag, or None if no baseline.
    """
    try:
        if not BASELINE_FILE.exists():
            return None
        baseline = json.loads(BASELINE_FILE.read_text())
    except (json.JSONDecodeError, IOError):
        return None

    current = analyze_trades()
    if current.get("total_trades", 0) < 5:
        return None  # Not enough data to compare

    b_win = baseline.get("win_rate", 0)
    c_win = current.get("win_rate", 0)
    b_pnl = baseline.get("avg_pnl", 0)
    c_pnl = current.get("avg_pnl", 0)

    degraded = False
    reasons = []

    # Win rate dropped more than 15 percentage points
    if b_win > 0 and (b_win - c_win) > 15:
        degraded = True
        reasons.append(f"Win rate dropped: {b_win}% → {c_win}%")

    # Average PnL went significantly negative
    if b_pnl > 0 and c_pnl < -abs(b_pnl * 0.5):
        degraded = True
        reasons.append(f"Avg PnL degraded: {b_pnl} → {c_pnl}")

    return {
        "degraded": degraded,
        "summary": "; ".join(reasons) if reasons else "Metrics within acceptable range",
        "baseline": {"win_rate": b_win, "avg_pnl": b_pnl, "total_trades": baseline.get("total_trades", 0)},
        "current": {"win_rate": c_win, "avg_pnl": c_pnl, "total_trades": current.get("total_trades", 0)},
    }


def detect_regime() -> dict:
    """Classify current market regime based on recent trade patterns.

    Simple heuristic-based approach.
    """
    REGIME_DEFAULTS = {"regime": "unknown", "confidence": 0, "updated_at": 0}

    current = analyze_trades(lookback_hours=48)
    if current.get("total_trades", 0) < 3:
        return {**REGIME_DEFAULTS, "note": "Insufficient data"}

    win_rate = current.get("win_rate", 50)
    avg_pnl = current.get("avg_pnl", 0)

    if win_rate > 65 and avg_pnl > 0:
        regime = "favorable"
        confidence = min(90, int(win_rate))
    elif win_rate < 35 or avg_pnl < -0.05:
        regime = "adverse"
        confidence = min(90, int(100 - win_rate))
    else:
        regime = "neutral"
        confidence = 50

    result = {
        "regime": regime,
        "confidence": confidence,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "updated_at": time.time(),
    }

    # Save regime state
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        REGIME_FILE.write_text(json.dumps(result, indent=2))
    except IOError:
        pass

    return result


def update_baseline() -> dict:
    """Save current metrics as the new performance baseline."""
    current = analyze_trades()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(current, indent=2))

    return current
