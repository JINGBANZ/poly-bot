# Phase 55 Reflection: Autonomous Trading Loop

## Reflection Questions

### What's the biggest risk of fully autonomous trading?
The biggest risk is **unintended execution at scale during extreme market events or LLM failure modes**. If the LLM misinterprets a breaking news event (e.g., confusing a "rumor denied" with a "rumor confirmed") and the bot has high enough limits, it could wipe out a portfolio before a human can intervene. Additionally, "fat-finger" errors in the code (like incorrect sizing or asset selection) are executed instantly.

### How do we prevent the LLM from hallucinating edge where none exists?
We use **negative constraints and multi-step verification**:
1.  **System Prompting:** Explicitly telling the LLM to be skeptical, that "No good trades" is a valid answer, and requiring "verifiable data points" rather than "vibes."
2.  **Separate Thesis Generation:** Forcing the LLM to write a 3-sentence thesis *after* research but *before* entry. If it can't cite a specific data point, it must return `NO_THESIS`.
3.  **Liquidity Guardrails:** Hallucinated edge often appears in illiquid markets where the spread is huge. By enforcing volume and depth requirements, we naturally filter out many "garbage" signals.

### What circuit breakers should exist?
1.  **Sizing Caps:** The current $2 max position is a great "soft" circuit breaker.
2.  **Portfolio Drawdown Limit:** A hard stop on all trading if the daily or total portfolio loss exceeds a certain percentage (e.g., 20%).
3.  **Velocity Limit:** Capping the number of trades per hour/day to prevent "looping" or rapid-fire errors.
4.  **Balance Floor:** Ensuring the bot never spends the last few dollars required for gas or account maintenance.
5.  **Human Kill-switch:** A simple way (like an environment variable or a specific file) to instantly disable trading while keeping the monitoring loop alive.
