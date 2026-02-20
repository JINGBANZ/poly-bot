# Polymarket CTF Redemption Research

*Last updated: 2026-02-19*

## 1. Contract Addresses (Polygon Mainnet)

| Contract | Address |
|----------|---------|
| **Conditional Tokens (CTF)** | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| **CTF Exchange** | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| **NegRisk CTF Exchange** | (for multi-outcome markets, separate contract) |
| **USDC.e (Collateral)** | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |

**Redemption calls go to the CTF contract** (`0x4D97...6045`), NOT the exchange.

## 2. redeemPositions() Function

### ABI
```json
[{
  "constant": false,
  "inputs": [
    {"name": "collateralToken", "type": "address"},
    {"name": "parentCollectionId", "type": "bytes32"},
    {"name": "conditionId", "type": "bytes32"},
    {"name": "indexSets", "type": "uint256[]"}
  ],
  "name": "redeemPositions",
  "outputs": [],
  "payable": false,
  "stateMutability": "nonpayable",
  "type": "function"
}]
```

### Parameters

| Parameter | Value for Polymarket |
|-----------|---------------------|
| `collateralToken` | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` (USDC.e) |
| `parentCollectionId` | `0x0000000000000000000000000000000000000000000000000000000000000000` (always zero for Polymarket) |
| `conditionId` | The market's condition ID (bytes32) — get from Polymarket API |
| `indexSets` | `[1, 2]` for binary markets (redeems both outcomes; only winner pays) |

### Key Notes
- **No amount parameter** — burns your ENTIRE balance for that condition
- Only works AFTER market resolution (oracle calls `reportPayouts()`)
- Winning tokens → $1.00 USDC.e each; losing tokens → $0
- No deadline — tokens are redeemable forever after resolution

## 3. Working Python Code (Official Polymarket Example)

Source: `https://github.com/Polymarket/conditional-token-examples-py`

```python
import os
from web3 import Web3
from web3.constants import HASH_ZERO
from web3.middleware import geth_poa_middleware, construct_sign_and_send_raw_middleware
from web3.gas_strategies.time_based import fast_gas_price_strategy
from dotenv import load_dotenv

load_dotenv()

REDEEM_ABI = [{"constant":False,"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"payable":False,"stateMutability":"nonpayable","type":"function"}]

def redeem(condition_id: str):
    pk = os.getenv("PK")
    rpc_url = os.getenv("RPC_URL")

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    w3.middleware_onion.add(construct_sign_and_send_raw_middleware(pk))
    w3.eth.default_account = w3.eth.account.from_key(pk).address
    w3.eth.set_gas_price_strategy(fast_gas_price_strategy)

    usdc = w3.to_checksum_address("0x2791bca1f2de4661ed88a30c99a7a9449aa84174")
    ctf = w3.eth.contract(
        w3.to_checksum_address("0x4d97dcd97ec945f40cf65f87097ace5ea0476045"),
        abi=REDEEM_ABI
    )

    tx_hash = ctf.functions.redeemPositions(
        usdc,           # collateralToken
        HASH_ZERO,      # parentCollectionId (always 0x00...00)
        condition_id,   # conditionId
        [1, 2],         # indexSets for binary markets
    ).transact()

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Redeemed! TX: {w3.to_hex(tx_hash)}")
    return receipt
```

## 4. Gasless Redemption (Alternative — No MATIC Needed!)

**Key discovery:** Polymarket has a **Builder Relayer API** that handles gas fees for you.

- Docs: `https://docs.polymarket.com/developers/builders/relayer-client`
- Community CLI: `https://github.com/NocodeSolutions/polymarket-gasless-redeem-cli`
- **Polymarket pays all gas fees** when using the relayer
- Requires Builder API credentials (API key, secret, passphrase)

### How it works:
1. Encode the `redeemPositions()` calldata
2. Submit to Polymarket's relayer API endpoint
3. Relayer submits the tx on-chain and pays gas
4. Zero gas cost for the user

**This is the recommended approach** — avoids the need for MATIC/POL entirely.

## 5. Gas Costs (If Paying Directly)

If calling the CTF contract directly (not using relayer):
- **Typical gas:** ~100,000-200,000 gas units for `redeemPositions()`
- **Polygon gas price:** ~30-50 gwei typically
- **Cost:** ~0.003-0.01 POL ($0.001-$0.005 at current prices)
- **Extremely cheap** — even 0.01 POL is enough for multiple redemptions

## 6. MATIC/POL Faucets (If Needed)

| Faucet | URL | Amount |
|--------|-----|--------|
| **Stakely** | `https://stakely.io/faucet/polygon-matic` | Small amount, mainnet |
| **Polygon Discord** | Official faucet in Discord server | Small amount |
| **Alchemy** | `https://www.alchemy.com/faucets/polygon-mainnet` | For devs |

**However:** Since the gasless relayer exists, faucets may not be needed at all.

## 7. Recommended Implementation Path

### Option A: Gasless via Relayer (Preferred)
1. Get Builder API credentials from Polymarket
2. Use the relayer to submit redeem transactions
3. No MATIC needed, no gas costs
4. Reference: `polymarket-gasless-redeem-cli` repo

### Option B: Direct On-Chain (Fallback)
1. Need wallet with private key + small amount of POL for gas
2. Call `redeemPositions()` directly on CTF contract
3. Use the Python code above with web3.py
4. Need: RPC URL (e.g., Alchemy/Infura), private key, condition ID

### Getting conditionId
- From Polymarket CLOB API: market data includes `condition_id`
- From on-chain: events on the CTF contract
- From Polymarket Gamma API: `/markets` endpoint returns condition IDs

## 8. Summary

| Question | Answer |
|----------|--------|
| CTF Contract | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| Function | `redeemPositions(address, bytes32, bytes32, uint256[])` |
| Can call with web3.py? | **Yes** — official example exists |
| Need MATIC? | **No** if using gasless relayer; ~0.01 POL if calling directly |
| Gas cost | ~$0.001-0.005 on Polygon |
| Key params | collateral=USDC.e, parent=0x0, conditionId from API, indexSets=[1,2] |
