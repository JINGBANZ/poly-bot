"""Auto-redemption module — claim resolved positions via Builder Relayer.

Scans data-api for redeemable positions and redeems them gaslessly
through Polymarket's Builder Relayer infrastructure.

Architecture:
- Pure Python PROXY relay implementation (no Node.js dependency)
- Called from the daemon's main loop after resolution checks
- Tracks redeemed positions in state/redemptions.json
- Writes alerts on successful redemption

Requirements:
- Builder API credentials in the bot's .polymarket-env file (see config.POLYMARKET_ENV_FILE)
- Python packages: eth-account, eth-abi, web3, py-builder-signing-sdk
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from py_builder_signing_sdk.config import BuilderConfig
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

from .logger import log
from . import config

# ── Constants ────────────────────────────────────────────────────────

RELAYER_URL = "https://relayer-v2.polymarket.com"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Polygon mainnet contract addresses
PROXY_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
RELAY_HUB = "0xD216153c06E857cD7f72665E0aF1d7D82172F494"
PROXY_INIT_CODE_HASH = "0xd21df8dc65880a8606f09fe0ce3df9b8869287ab0b058be05aa9e8af6330a00b"

DEFAULT_GAS_LIMIT = 10_000_000

ENV_FILE = config.POLYMARKET_ENV_FILE
REDEMPTIONS_FILE = os.path.join(config.STATE_DIR, "redemptions.json")

# Cooldown: don't attempt redemption more than once per 10 minutes per condition
_last_attempt = {}  # condition_id -> timestamp
ATTEMPT_COOLDOWN = 600  # seconds

w3 = Web3()

# ── ABI Encoding Helpers ─────────────────────────────────────────────

# redeemPositions(address collateralToken, bytes32 parentCollectionId,
#                 bytes32 conditionId, uint256[] indexSets)
REDEEM_SELECTOR = w3.keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]

# proxy((uint8,address,uint256,bytes)[])
PROXY_SELECTOR = w3.keccak(text="proxy((uint8,address,uint256,bytes)[])")[:4]


def _encode_redeem_calldata(condition_id: str) -> bytes:
    """Encode redeemPositions(USDC, 0x0, conditionId, [1, 2])."""
    params = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            USDC_ADDRESS,
            b"\x00" * 32,
            bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id),
            [1, 2],
        ],
    )
    return REDEEM_SELECTOR + params


def _encode_proxy_calldata(calls: list[dict]) -> bytes:
    """Encode proxy(calls) where calls is [(typeCode, to, value, data), ...].

    The proxy function ABI: proxy((uint8,address,uint256,bytes)[])
    """
    tuples = []
    for c in calls:
        type_code = int(c.get("typeCode", 1))  # 1 = Call
        to = Web3.to_checksum_address(c["to"])
        value = int(c.get("value", 0))
        data = bytes.fromhex(c["data"][2:]) if isinstance(c["data"], str) and c["data"].startswith("0x") else c["data"]
        tuples.append((type_code, to, value, data))

    params = abi_encode(["(uint8,address,uint256,bytes)[]"], [tuples])
    return PROXY_SELECTOR + params


# ── Crypto Helpers ───────────────────────────────────────────────────

def _derive_proxy_wallet(owner: str, factory: str) -> str:
    """Derive the CREATE2 proxy wallet address."""
    owner_cs = Web3.to_checksum_address(owner)
    salt = w3.keccak(abi_encode(["address"], [owner_cs]))
    # CREATE2: keccak256(0xff ++ factory ++ salt ++ initCodeHash)
    raw = b"\xff" + bytes.fromhex(factory[2:]) + salt + bytes.fromhex(PROXY_INIT_CODE_HASH[2:])
    addr_hash = w3.keccak(raw)
    return Web3.to_checksum_address("0x" + addr_hash[-20:].hex())


def _create_struct_hash(
    from_addr: str, to_addr: str, data: bytes,
    tx_fee: str, gas_price: str, gas_limit: str,
    nonce: str, relay_hub: str, relay_addr: str,
) -> bytes:
    """Create the GSN-style struct hash for PROXY signing.

    Matches the TS implementation: concat("rlx:" + from + to + data +
    txFee(32) + gasPrice(32) + gasLimit(32) + nonce(32) +
    relayHubAddress + relayAddress) then keccak256.
    """
    prefix = b"rlx:"
    parts = [
        prefix,
        bytes.fromhex(from_addr[2:].lower()),
        bytes.fromhex(to_addr[2:].lower()),
        data,
        int(tx_fee).to_bytes(32, "big"),
        int(gas_price).to_bytes(32, "big"),
        int(gas_limit).to_bytes(32, "big"),
        int(nonce).to_bytes(32, "big"),
        bytes.fromhex(relay_hub[2:].lower()),
        bytes.fromhex(relay_addr[2:].lower()),
    ]
    return w3.keccak(b"".join(parts))


def _sign_struct_hash(private_key: str, struct_hash: bytes) -> str:
    """Sign the struct hash as a raw message (personal_sign of raw bytes).

    The TS code does: signer.signMessage({ raw: toBytes(message) })
    which is an EIP-191 personal sign of the raw bytes.
    """
    msg = encode_defunct(primitive=struct_hash)
    signed = Account.sign_message(msg, private_key=private_key)
    return signed.signature.hex()


# ── Builder Auth ─────────────────────────────────────────────────────

def _make_builder_config(env: dict) -> BuilderConfig:
    """Create BuilderConfig from env vars."""
    creds = BuilderApiKeyCreds(
        key=env["POLYMARKET_BUILDER_API_KEY"],
        secret=env["POLYMARKET_BUILDER_API_SECRET"],
        passphrase=env["POLYMARKET_BUILDER_PASSPHRASE"],
    )
    return BuilderConfig(local_builder_creds=creds)


def _authed_headers(builder_config: BuilderConfig, method: str, path: str, body: str = None) -> dict:
    """Generate builder auth headers."""
    payload = builder_config.generate_builder_headers(method, path, body)
    if payload is None:
        raise RuntimeError("Failed to generate builder auth headers")
    return payload.to_dict()


# ── Relay API ────────────────────────────────────────────────────────

def _get_relay_payload(address: str) -> dict:
    """GET /relay-payload?address=X&type=PROXY — returns {address, nonce}."""
    r = requests.get(
        f"{RELAYER_URL}/relay-payload",
        params={"address": address, "type": "PROXY"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _submit_transaction(builder_config: BuilderConfig, request_body: dict) -> dict:
    """POST /submit with builder auth headers."""
    body_str = json.dumps(request_body)
    headers = _authed_headers(builder_config, "POST", "/submit", body_str)
    headers["Content-Type"] = "application/json"
    r = requests.post(
        f"{RELAYER_URL}/submit",
        data=body_str,
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _get_transaction(builder_config: BuilderConfig, tx_id: str) -> list:
    """GET /transaction?id=X with builder auth."""
    headers = _authed_headers(builder_config, "GET", "/transaction")
    r = requests.get(
        f"{RELAYER_URL}/transaction",
        params={"id": tx_id},
        headers=headers,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _poll_until_mined(builder_config: BuilderConfig, tx_id: str,
                      max_polls: int = 20, poll_interval: float = 3.0) -> dict | None:
    """Poll transaction status until mined/confirmed or failed."""
    success_states = {"STATE_MINED", "STATE_CONFIRMED"}
    fail_states = {"STATE_FAILED"}

    for _ in range(max_polls):
        try:
            txns = _get_transaction(builder_config, tx_id)
            if txns and len(txns) > 0:
                txn = txns[0]
                state = txn.get("state", "")
                if state in success_states:
                    return txn
                if state in fail_states:
                    log(f"  ❌ Relay tx {tx_id} failed on-chain: {txn.get('transactionHash', '')}")
                    return None
        except Exception as e:
            log(f"  ⚠️ Poll error for {tx_id}: {e}")
        time.sleep(poll_interval)

    log(f"  ⚠️ Relay tx {tx_id} timed out after {max_polls} polls")
    return None


# ── Core Redemption Logic ────────────────────────────────────────────

def _build_proxy_request(
    private_key: str, from_addr: str, proxy_calldata: bytes,
    relay_payload: dict, metadata: str = "",
) -> dict:
    """Build the full PROXY transaction request (equivalent to TS buildProxyTransactionRequest)."""
    to = PROXY_FACTORY
    relay_addr = relay_payload["address"]
    nonce = str(relay_payload["nonce"])
    gas_limit = str(DEFAULT_GAS_LIMIT)
    relayer_fee = "0"
    gas_price = "0"

    # Hex-encode the proxy calldata
    data_hex = "0x" + proxy_calldata.hex()

    struct_hash = _create_struct_hash(
        from_addr=from_addr,
        to_addr=to,
        data=proxy_calldata,
        tx_fee=relayer_fee,
        gas_price=gas_price,
        gas_limit=gas_limit,
        nonce=nonce,
        relay_hub=RELAY_HUB,
        relay_addr=relay_addr,
    )

    signature = _sign_struct_hash(private_key, struct_hash)
    if not signature.startswith("0x"):
        signature = "0x" + signature

    proxy_wallet = _derive_proxy_wallet(from_addr, PROXY_FACTORY)

    return {
        "from": from_addr,
        "to": to,
        "proxyWallet": proxy_wallet,
        "data": data_hex,
        "nonce": nonce,
        "signature": signature,
        "signatureParams": {
            "gasPrice": gas_price,
            "gasLimit": gas_limit,
            "relayerFee": relayer_fee,
            "relayHub": RELAY_HUB,
            "relay": relay_addr,
        },
        "type": "PROXY",
        "metadata": metadata,
    }


def _execute_redemption(condition_id: str, env: dict) -> dict:
    """Execute a single PROXY redemption in pure Python.

    Returns {"success": bool, "tx_hash": str, "error": str}
    """
    private_key = env.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        return {"success": False, "error": "missing POLYMARKET_PRIVATE_KEY"}

    # Derive address from private key
    acct = Account.from_key(private_key)
    from_addr = acct.address

    builder_config = _make_builder_config(env)

    # Step 1: Encode the redeemPositions calldata
    redeem_data = _encode_redeem_calldata(condition_id)

    # Step 2: Wrap in proxy multicall
    calls = [{"typeCode": 1, "to": CTF_ADDRESS, "value": 0, "data": redeem_data}]
    proxy_data = _encode_proxy_calldata(calls)

    # Step 3: Get relay payload (address + nonce)
    relay_payload = _get_relay_payload(from_addr)

    # Step 4: Build and sign the request
    request_body = _build_proxy_request(
        private_key=private_key,
        from_addr=from_addr,
        proxy_calldata=proxy_data,
        relay_payload=relay_payload,
        metadata=f"Auto-redeem {condition_id}",
    )

    # Step 5: Submit
    resp = _submit_transaction(builder_config, request_body)
    tx_id = resp.get("transactionID") or resp.get("transactionId", "")
    if not tx_id:
        return {"success": False, "error": f"No transaction ID in response: {resp}"}

    log(f"  📡 Relay submitted: {tx_id}")

    # Step 6: Poll until mined
    result = _poll_until_mined(builder_config, tx_id)
    if result:
        tx_hash = result.get("transactionHash", "")
        return {"success": True, "tx_hash": tx_hash}
    else:
        return {"success": False, "error": f"Transaction {tx_id} failed or timed out"}


# ── Env / State Helpers ──────────────────────────────────────────────

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


# ── Public API ───────────────────────────────────────────────────────

def get_redeemable_positions() -> list:
    """Fetch positions from data-api that are marked redeemable."""
    env = _load_env()
    funder = env.get("POLYMARKET_FUNDER", "").lower()
    if not funder:
        return []

    try:
        r = requests.get(
            f"https://data-api.polymarket.com/positions?user={funder}&sizeThreshold=0",
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
    """Redeem a single position via pure Python PROXY relay.

    Returns {"success": bool, "tx_hash": str, "error": str}
    """
    # Cooldown check
    now = time.time()
    last = _last_attempt.get(condition_id, 0)
    if now - last < ATTEMPT_COOLDOWN:
        return {"success": False, "error": "cooldown"}
    _last_attempt[condition_id] = now

    env = _load_env()

    # Validate required env vars
    required = ["POLYMARKET_PRIVATE_KEY", "POLYMARKET_BUILDER_API_KEY",
                "POLYMARKET_BUILDER_API_SECRET", "POLYMARKET_BUILDER_PASSPHRASE"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        return {"success": False, "error": f"Missing env vars: {', '.join(missing)}"}

    try:
        result = _execute_redemption(condition_id, env)

        if not result["success"] and ("401" in str(result.get("error", ""))
                                       or "authorization" in str(result.get("error", "")).lower()):
            return {"success": False, "error": "builder_auth_failed", "details": result.get("error", "")}

        return result

    except requests.exceptions.HTTPError as e:
        error_text = str(e)
        if hasattr(e, 'response') and e.response is not None:
            error_text = f"{e.response.status_code}: {e.response.text[:500]}"
        if "401" in error_text or "authorization" in error_text.lower():
            return {"success": False, "error": "builder_auth_failed", "details": error_text}
        return {"success": False, "error": error_text}
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
