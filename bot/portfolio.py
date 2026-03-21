"""Portfolio module — position tracking, P&L, and state management."""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from . import config

log = logging.getLogger(__name__)


def _safe_float(value, default: float = 0.0) -> float:
    """Convert value to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Position:
    """A single position with computed fields."""
    def __init__(self, raw: dict):
        self.raw = raw
        self.title = (raw.get("title") or raw.get("slug", "unknown"))[:50]
        self.slug = raw.get("slug", "")
        self.outcome = raw.get("outcome", "Yes")
        self.size = _safe_float(raw.get("size", 0))
        self.entry = _safe_float(raw.get("avgPrice", 0))
        self.current = _safe_float(raw.get("curPrice", 0))
        self.token_id = raw.get("asset", "")
        self.condition_id = raw.get("conditionId", "")
        self.end_date = raw.get("endDate", "")

    @property
    def cost(self) -> float:
        return self.size * self.entry

    @property
    def value(self) -> float:
        return self.size * self.current

    @property
    def pnl(self) -> float:
        return self.value - self.cost

    @property
    def pnl_pct(self) -> float:
        return (self.current - self.entry) / self.entry if self.entry else 0

    @property
    def emoji(self) -> str:
        if self.pnl_pct >= 0.5:
            return "🚀"
        elif self.pnl_pct >= 0:
            return "🟢"
        elif self.pnl_pct >= -0.3:
            return "🟡"
        else:
            return "🔴"

    def __repr__(self):
        return f"{self.emoji} {self.title:40s} | {self.size:.1f} @ {self.entry:.3f} → {self.current:.3f} ({self.pnl_pct:+.1%})"


class Portfolio:
    """Collection of positions with aggregate stats."""
    def __init__(self, positions: list[Position]):
        self.positions = positions

    @classmethod
    def from_api(cls, raw_positions: list[dict]):
        return cls([Position(p) for p in raw_positions])

    @property
    def total_cost(self) -> float:
        return sum(p.cost for p in self.positions)

    @property
    def total_value(self) -> float:
        return sum(p.value for p in self.positions)

    @property
    def total_pnl(self) -> float:
        return self.total_value - self.total_cost

    @property
    def total_pnl_pct(self) -> float:
        return self.total_pnl / self.total_cost if self.total_cost else 0

    def summary(self) -> str:
        lines = [f"📊 {len(self.positions)} positions | Cost: ${self.total_cost:.2f} | Value: ${self.total_value:.2f} | PnL: ${self.total_pnl:+.2f} ({self.total_pnl_pct:+.1%})"]
        for p in self.positions:
            lines.append(f"  {p}")
        return "\n".join(lines)

    def active(self) -> list["Position"]:
        """Return positions with size > 0 (filters out stale zero-size entries)."""
        return [p for p in self.positions if p.size > 0]

    def save(self):
        """Save current state to positions.json atomically.

        Writes to a temporary file first, then renames.  This prevents
        half-written / corrupted state if the process is killed mid-write.
        """
        os.makedirs(config.STATE_DIR, exist_ok=True)
        state = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "count": len(self.positions),
                "cost": round(self.total_cost, 2),
                "value": round(self.total_value, 2),
                "pnl": round(self.total_pnl, 2),
                "pnl_pct": round(self.total_pnl_pct * 100, 1),
            },
            "positions": [{
                "title": p.title,
                "slug": p.slug,
                "outcome": p.outcome,
                "size": p.size,
                "entry_price": p.entry,
                "current_price": p.current,
                "cost": round(p.cost, 2),
                "value": round(p.value, 2),
                "pnl_pct": round(p.pnl_pct * 100, 1),
                "condition_id": p.condition_id,
                "token_id": p.token_id,
                "end_date": p.end_date,
            } for p in self.positions],
        }
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=config.STATE_DIR, suffix=".tmp", prefix="positions_"
            )
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, config.POS_FILE)
        except OSError:
            log.exception("Failed to save positions to %s", config.POS_FILE)
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
