"""Post-mortem system — auto-analyze resolved positions."""

import json
import os
from datetime import datetime, timezone
from . import config
from .logger import log
from .alerts import write_alert

POSTMORTEM_DIR = os.path.join(config.ANALYSIS_DIR, "postmortems")


def generate_postmortem(position, won: bool | None, winner: str = "") -> str | None:
    """Generate a post-mortem analysis for a resolved position using LLM.
    
    Args:
        position: Position object (from portfolio.py)
        won: True if we won, False if lost, None if unknown
        winner: The winning outcome string
    
    Returns:
        The post-mortem text, or None on failure.
    """
    from . import llm
    
    outcome_str = "WON" if won is True else ("LOST" if won is False else "UNKNOWN")
    pnl = position.size - position.cost if won else -position.cost if won is False else 0
    
    prompt = f"""Write a post-mortem analysis for this resolved Polymarket trade:

Market: {position.title}
Our side: {outcome_str} ({position.outcome})
Winning outcome: {winner or 'unknown'}
Entry price: {position.entry:.2f} ({position.entry*100:.0f}¢)
Final price: {position.current:.2f} ({position.current*100:.0f}¢)
Size: {position.size:.1f} shares
Cost basis: ${position.cost:.2f}
P&L: ${pnl:+.2f}
Resolution date: {position.end_date or 'unknown'}

Analyze:
1. THESIS REVIEW: What was the likely thesis for entering this trade? Was it sound?
2. OUTCOME: Why did the market resolve this way? What drove the outcome?
3. SIGNALS MISSED: What information or signals could have predicted this outcome earlier?
4. LESSONS: What should we learn from this trade for future decisions?
5. GRADE: Rate the trade A/B/C/D/F based on process quality (not just outcome).

Be honest and specific. A lucky win on a bad thesis is still a D."""

    system = """You are a trading post-mortem analyst. Be brutally honest about trade quality.
Focus on PROCESS over OUTCOME. A good process that lost is better than a bad process that won.
Keep it concise — 200 words max."""

    result = llm.call(prompt, system=system, temperature=0.3, max_tokens=1024)
    if not result:
        return None
    
    # Save to file
    _save_postmortem(position, outcome_str, pnl, result)
    
    # Write alert summary
    first_line = result.split('\n')[0][:100]
    grade_line = [l for l in result.split('\n') if 'GRADE' in l.upper() or any(g in l for g in ['A', 'B', 'C', 'D', 'F'])]
    grade = grade_line[-1].strip()[:60] if grade_line else ""
    
    summary = f"📋 POST-MORTEM: {position.title}\n{outcome_str} | P&L: ${pnl:+.2f}\n{grade}"
    write_alert(summary)
    
    return result


def _save_postmortem(position, outcome: str, pnl: float, analysis: str):
    """Save post-mortem to analysis/postmortems/."""
    os.makedirs(POSTMORTEM_DIR, exist_ok=True)
    
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = position.slug or position.title[:30].replace(" ", "_").replace("/", "-")
    filename = f"{ts}_{slug}.md"
    filepath = os.path.join(POSTMORTEM_DIR, filename)
    
    content = f"""# Post-Mortem: {position.title}

**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
**Outcome:** {outcome}
**P&L:** ${pnl:+.2f}
**Entry:** {position.entry:.2f} → **Final:** {position.current:.2f}
**Size:** {position.size:.1f} shares | **Cost:** ${position.cost:.2f}

---

{analysis}
"""
    
    try:
        with open(filepath, "w") as f:
            f.write(content)
        log(f"  📋 Post-mortem saved: {filename}")
    except Exception as e:
        log(f"  ⚠️ Failed to save post-mortem: {e}")
