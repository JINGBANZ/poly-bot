"""Auto-redemption module — claim resolved positions via Builder Relayer.

Scans data-api for redeemable positions and redeems them gaslessly
through Polymarket's Builder Relayer infrastructure.

Architecture:
- Uses the Node.js @polymarket/builder-relayer-client (PROXY tx type)
- Called from the daemon's main loop after resolution checks
- Tracks redeemed positions in state/redemptions.json
- Writes alerts on successful redemption

Requirements:
- Node.js scripts at: polymarket-bot/scripts/redeem_auto.mjs
- Builder API credentials in /home/ubuntu/.openclaw/.polymarket-env
- npm packages installed in polymarket-bot/scripts/node_modules/
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone

from .logger import log
from . import config

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
REDEEM_SCRIPT = os.path.join(SCRIPTS_DIR, "redeem_auto.mjs")
ENV_FILE = "/home/ubuntu/.openclaw/.polymarket-env"
REDEMPTIONS_FILE = os.path.join(config.STATE_DIR, "redemptions.json")

# Cooldown: don't attempt redemption more than once per 10 minutes per condition
_last_attempt = {}  # condition_id -> timestamp
ATTEMPT_COOLDOWN = 600  # seconds


def _load_env():
    """Load polymarket env vars."""
    env = os.environ.copy()
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k] = v
    return env


def _load_redemptions():
    """Load redemption history."""
    try:
        with open(REDEMPTIONS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"total": 0, "entries": []}


def _save_redemptions(data):
    """Save redemption history."""
    os.makedirs(os.path.dirname(REDEMPTIONS_FILE), exist_ok=True)
    with open(REDEMPTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _already_redeemed(condition_id: str) -> bool:
    """Check if a condition was already redeemed."""
    data = _load_redemptions()
    return any(
        e.get("condition_id") == condition_id
        for e in data.get("entries", [])
    )


def _record_redemption(title: str, condition_id: str, shares: float, payout: float, tx_hash: str = ""):
    """Record a successful redemption."""
    data = _load_redemptions()
    data["entries"].append({
        "market": title,
        "condition_id": condition_id,
        "shares": shares,
        "payout": payout,
        "tx_hash": tx_hash,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })
    data["total"] = sum(e.get("payout", 0) for e in data["entries"])
    _save_redemptions(data)


def get_redeemable_positions() -> list:
    """Fetch positions from data-api that are marked redeemable."""
    import requests
    env = _load_env()
    funder = env.get("POLYMARKET_FUNDER", "").lower()
    if not funder:
        return []

    try:
        r = requests.get(
            f"https://data-api.polymarket.com/positions?user={funder}",
            timeout=15
        )
        r.raise_for_status()
        positions = r.json()
        return [
            p for p in positions
            if p.get("redeemable") and float(p.get("size", 0)) > 0
            and not _already_redeemed(p.get("conditionId", ""))
        ]
    except Exception as e:
        log(f"  ⚠️ Redeemer: failed to fetch positions: {e}")
        return []


def redeem_position(condition_id: str, title: str = "") -> dict:
    """Redeem a single position via the Node.js script.
    
    Returns {"success": bool, "tx_hash": str, "error": str}
    """
    # Cooldown check
    now = time.time()
    last = _last_attempt.get(condition_id, 0)
    if now - last < ATTEMPT_COOLDOWN:
        return {"success": False, "error": "cooldown"}
    _last_attempt[condition_id] = now

    if not os.path.exists(REDEEM_SCRIPT):
        return {"success": False, "error": f"Script not found: {REDEEM_SCRIPT}"}

    env = _load_env()
    env["PATH"] = os.environ.get("PATH", "/usr/bin")

    try:
        result = subprocess.run(
            ["node", REDEEM_SCRIPT, condition_id],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=SCRIPTS_DIR,
            env=env,
        )

        output = result.stdout + result.stderr
        
        if result.returncode == 0 and "SUCCESS" in output:
            # Parse tx hash from output
            tx_hash = ""
            for line in output.splitlines():
                if "txHash" in line or "transactionHash" in line:
                    # Try to extract hash
                    import re
                    match = re.search(r'0x[a-fA-F0-9]{64}', line)
                    if match:
                        tx_hash = match.group(0)
                        break
            
            return {"success": True, "tx_hash": tx_hash, "output": output}
        else:
            # Check for auth errors specifically
            if "invalid authorization" in output or "401" in output:
                return {"success": False, "error": "builder_auth_failed", "output": output}
            return {"success": False, "error": output[-500:] if output else "unknown"}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def check_and_redeem(dry_run=False) -> list:
    """Main entry point: check for redeemable positions and redeem them.
    
    Called from the daemon's main loop.
    Returns list of results.
    """
    redeemable = get_redeemable_positions()
    if not redeemable:
        return []

    results = []
    for pos in redeemable:
        condition_id = pos.get("conditionId", "")
        title = pos.get("title", "Unknown")
        size = float(pos.get("size", 0))
        
        log(f"  💰 Redeemable: {title} ({size:.2f} shares)")

        if dry_run:
            results.append({"title": title, "size": size, "action": "dry_run"})
            continue

        result = redeem_position(condition_id, title)
        
        if result["success"]:
            _record_redemption(
                title=title,
                condition_id=condition_id,
                shares=size,
                payout=size,  # Winning shares = $1 each
                tx_hash=result.get("tx_hash", ""),
            )
            from .alerts import write_alert
            write_alert(f"💰 REDEEMED: {title} — ${size:.2f} claimed!")
            log(f"  ✅ Redeemed {title}: ${size:.2f}")
        elif result.get("error") == "cooldown":
            pass  # Silent
        elif result.get("error") == "builder_auth_failed":
            log(f"  ⚠️ Redemption auth failed — Builder credentials may need refresh")
            # Only log once per session
            _last_attempt[condition_id] = time.time() + 3600  # Suppress for 1 hour
        else:
            log(f"  ❌ Redemption failed: {result.get('error', 'unknown')[:200]}")

        results.append({"title": title, "size": size, **result})

    return results
