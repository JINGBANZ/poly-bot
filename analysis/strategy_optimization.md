# Strategy Parameter Optimization

**Date:** 2026-02-24 08:04 UTC
**Total resolved markets available:** 1498

## 1. Price Point Analysis

For each resolved market, we simulate buying YES at various fixed prices.
Win = market resolved YES. Loss = market resolved NO.

| Price | Markets | Wins | Win Rate | Avg PnL | EV/$1 | Break-Even WR |
|-------|---------|------|----------|---------|-------|----------------|
| 10¢ | 1498 | 363 | 24.2% | +142.3% | $+1.423 | 10% |
| 15¢ | 1498 | 363 | 24.2% | +61.5% | $+0.615 | 15% |
| 20¢ | 1498 | 363 | 24.2% | +21.2% | $+0.212 | 20% |
| 25¢ | 1498 | 363 | 24.2% | -3.1% | $-0.031 | 25% |
| 30¢ | 1498 | 363 | 24.2% | -19.2% | $-0.192 | 30% |
| 35¢ | 1498 | 363 | 24.2% | -30.8% | $-0.308 | 35% |
| 40¢ | 1498 | 363 | 24.2% | -39.4% | $-0.394 | 40% |
| 45¢ | 1498 | 363 | 24.2% | -46.2% | $-0.462 | 45% |

## 2. Value Zone Optimization

Testing different value zone ranges (using markets with price history):

| Zone | Markets | Win Rate | Avg PnL | EV/$1 |
|------|---------|----------|---------|-------|
| 10-25¢ | 3 | 33.3% | +208.6% | $+2.086 |
| 10-30¢ | 3 | 33.3% | +208.6% | $+2.086 |
| 10-35¢ | 4 | 25.0% | +131.5% | $+1.315 |
| 10-40¢ | 4 | 25.0% | +131.5% | $+1.315 |
| 10-45¢ | 4 | 25.0% | +131.5% | $+1.315 |
| 15-35¢ | 2 | 0.0% | -100.0% | $-1.000 |
| 15-40¢ | 2 | 0.0% | -100.0% | $-1.000 |
| 15-45¢ | 2 | 0.0% | -100.0% | $-1.000 |
| 20-40¢ | 2 | 0.0% | -100.0% | $-1.000 |
| 20-45¢ | 2 | 0.0% | -100.0% | $-1.000 |

## 3. Category Performance

| Category | Count | Win Rate | EV/$1 | Recommendation |
|----------|-------|----------|-------|----------------|
| other | 3 | 33% | $+2.086 | ✅ Keep |
| sports | 1 | 0% | $-1.000 | ❓ Low sample |

## 4. Volume Correlation

| Volume Tier | Count | Win Rate | EV/$1 |
|-------------|-------|----------|-------|
| 10M+ | 4 | 25% | $+1.315 |
