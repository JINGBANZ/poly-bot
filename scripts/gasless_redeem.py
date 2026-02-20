#!/usr/bin/env python3
"""
Gasless redemption of Polymarket proxy wallet positions.
Uses the Polymarket relayer v2 with PROXY transaction type.
"""

import json, time, requests
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams
from py_builder_signing_sdk.signing.hmac import build_hmac_signature

# === CONFIG ===
import os as _os
from dotenv import dotenv_values as _dotenv_values

_env = _dotenv_values("/home/ubuntu/.openclaw/.polymarket-env")
for _k, _v in _env.items():
    _os.environ.setdefault(_k, _v)

PRIVATE_KEY = _os.environ.get("POLYMARKET_PRIVATE_KEY", "")
FUNDER = _os.environ.get("POLYMARKET_FUNDER", "0x528d07F3b854Ab55cFdD86F34E73262dE218CED8")
RELAYER_URL = 'https://relayer-v2.polymarket.com'
PROXY_FACTORY = '0xaB45c5A4B0c941a2F231C04C3f49182e1A254052'
RELAY_HUB = '0xD216153c06E857cD7f72665E0aF1d7D82172F494'
CTF_CONTRACT = '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045'
USDC_E = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'

# Condition IDs
OXY_CONDITION = '0x688da28c436f9f49ee08df3afbe1d0de138de7a828478cf50eb9cc013766bdb3'
DASH_CONDITION = '0xe923673ba211c54d72ba450877492a9e39582897edb9e8bf48fd1b42db746561'

# ABIs
REDEEM_ABI = [{"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"stateMutability":"nonpayable","type":"function"}]

PROXY_ABI = [{"constant":False,"inputs":[{"components":[{"name":"typeCode","type":"uint8"},{"name":"to","type":"address"},{"name":"value","type":"uint256"},{"name":"data","type":"bytes"}],"name":"calls","type":"tuple[]"}],"name":"proxy","outputs":[{"name":"returnValues","type":"bytes[]"}],"payable":True,"stateMutability":"payable","type":"function"}]


def get_builder_headers(api_key, api_secret, api_passphrase, method, path, body=""):
    """Generate Builder auth headers."""
    timestamp = str(int(time.time()))
    sig = build_hmac_signature(api_secret, timestamp, method, path, body)
    return {
        'POLY_BUILDER_SIGNATURE': sig,
        'POLY_BUILDER_TIMESTAMP': timestamp,
        'POLY_BUILDER_API_KEY': api_key,
        'POLY_BUILDER_PASSPHRASE': api_passphrase,
        'Content-Type': 'application/json',
    }


def create_struct_hash(from_addr, to_addr, data, tx_fee, gas_price, gas_limit, nonce, relay_hub, relay_addr):
    """Create the GSN-style struct hash for signing."""
    w3 = Web3()
    
    # Concat: "rlx:" + from + to + data + txFee(32) + gasPrice(32) + gasLimit(32) + nonce(32) + relayHub + relay
    prefix = b'rlx:'
    from_bytes = bytes.fromhex(from_addr[2:].lower())
    to_bytes = bytes.fromhex(to_addr[2:].lower())
    data_bytes = bytes.fromhex(data[2:] if data.startswith('0x') else data)
    tx_fee_bytes = int(tx_fee).to_bytes(32, 'big')
    gas_price_bytes = int(gas_price).to_bytes(32, 'big')
    gas_limit_bytes = int(gas_limit).to_bytes(32, 'big')
    nonce_bytes = int(nonce).to_bytes(32, 'big')
    relay_hub_bytes = bytes.fromhex(relay_hub[2:].lower())
    relay_bytes = bytes.fromhex(relay_addr[2:].lower())
    
    # Addresses need to be padded to 20 bytes (they already are as hex addresses)
    concat = (prefix + from_bytes + to_bytes + data_bytes + 
              tx_fee_bytes + gas_price_bytes + gas_limit_bytes + 
              nonce_bytes + relay_hub_bytes + relay_bytes)
    
    return w3.keccak(concat)


def sign_struct_hash(struct_hash, private_key):
    """Sign the struct hash as a personal message."""
    # The TS code uses signMessage which does eth_sign (personal_sign)
    msg = encode_defunct(struct_hash)
    signed = Account.sign_message(msg, private_key)
    return signed.signature.hex()


def encode_proxy_calldata(transactions):
    """Encode the proxy() function call with transaction tuples."""
    w3 = Web3()
    proxy_contract = w3.eth.contract(abi=PROXY_ABI)
    
    # transactions is list of (typeCode, to, value, data)
    return proxy_contract.encode_abi('proxy', [transactions])


def encode_redeem_calldata(condition_id):
    """Encode redeemPositions() calldata."""
    w3 = Web3()
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_CONTRACT), abi=REDEEM_ABI)
    condition_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id)
    return ctf.encode_abi('redeemPositions', [
        Web3.to_checksum_address(USDC_E),
        b'\x00' * 32,
        condition_bytes,
        [1, 2]
    ])


def derive_proxy_wallet(eoa_address, factory_address):
    """Derive proxy wallet address using CREATE2."""
    w3 = Web3()
    PROXY_INIT_CODE_HASH = '0xd21df8dc65880a8606f09fe0ce3df9b8869287ab0b058be05aa9e8af6330a00b'
    
    # salt = keccak256(abi.encodePacked(address))
    salt = w3.keccak(bytes.fromhex(eoa_address[2:].lower().zfill(40)))
    
    # CREATE2: keccak256(0xff ++ factory ++ salt ++ initCodeHash)
    factory_bytes = bytes.fromhex(factory_address[2:])
    init_hash_bytes = bytes.fromhex(PROXY_INIT_CODE_HASH[2:])
    
    create2_input = b'\xff' + factory_bytes + salt + init_hash_bytes
    addr_hash = w3.keccak(create2_input)
    proxy_addr = Web3.to_checksum_address('0x' + addr_hash.hex()[-40:])
    return proxy_addr


def main():
    account = Account.from_key(PRIVATE_KEY)
    eoa = account.address
    print(f"EOA: {eoa}")
    
    # Derive proxy wallet and verify
    derived_proxy = derive_proxy_wallet(eoa, PROXY_FACTORY)
    print(f"Derived proxy: {derived_proxy}")
    print(f"Known funder:  {FUNDER}")
    print(f"Match: {derived_proxy.lower() == FUNDER.lower()}")
    
    if derived_proxy.lower() != FUNDER.lower():
        print("⚠️  Proxy derivation mismatch! Using known funder address.")
    
    # Use Builder creds from env for relayer auth
    import os
    api_key = os.environ.get('POLYMARKET_BUILDER_API_KEY', '')
    api_secret = os.environ.get('POLYMARKET_BUILDER_API_SECRET', '')
    api_passphrase = os.environ.get('POLYMARKET_BUILDER_PASSPHRASE', '')
    print(f"  Builder API key: {api_key[:8]}...")
    
    # CLOB client for balance checks
    clob = ClobClient('https://clob.polymarket.com', key=PRIVATE_KEY, chain_id=137, funder=FUNDER)
    creds = clob.derive_api_key()
    clob.set_api_creds(creds)
    
    # Pre-redemption balance
    ba = clob.get_balance_allowance(BalanceAllowanceParams(asset_type='COLLATERAL', signature_type=1))
    usdc_before = int(ba['balance']) / 1e6
    print(f"\n💰 USDC before: ${usdc_before:.6f}")
    
    # Check OXY balance
    oxy_ba = clob.get_balance_allowance(BalanceAllowanceParams(
        asset_type='CONDITIONAL', 
        token_id='99206042732582327149752420430322189152998581132203828422722078323956048439548',
        signature_type=1
    ))
    print(f"OXY YES tokens: {int(oxy_ba['balance'])/1e6}")
    
    # Encode redeem calldatas
    oxy_redeem_data = encode_redeem_calldata(OXY_CONDITION)
    dash_redeem_data = encode_redeem_calldata(DASH_CONDITION)
    
    # Build proxy transactions: (typeCode=1 for CALL, to, value, data)
    proxy_txns = [
        (1, Web3.to_checksum_address(CTF_CONTRACT), 0, bytes.fromhex(oxy_redeem_data[2:])),
        (1, Web3.to_checksum_address(CTF_CONTRACT), 0, bytes.fromhex(dash_redeem_data[2:])),
    ]
    
    # Encode proxy() calldata
    proxy_data = encode_proxy_calldata(proxy_txns)
    print(f"\nProxy calldata length: {len(proxy_data)} chars")
    
    # Get relay payload from relayer
    path = f'/relay-payload?address={eoa}&type=PROXY'
    headers = get_builder_headers(api_key, api_secret, api_passphrase, 'GET', path)
    
    print(f"\n📤 Getting relay payload...")
    r = requests.get(f'{RELAYER_URL}{path}', headers=headers, timeout=15)
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.text[:300]}")
    
    if r.status_code != 200:
        print("❌ Failed to get relay payload")
        return False
    
    relay_payload = r.json()
    relay_address = relay_payload.get('address', '')
    relay_nonce = relay_payload.get('nonce', 0)
    print(f"  Relay address: {relay_address}")
    print(f"  Nonce: {relay_nonce}")
    
    # Create struct hash - use 500K gas limit (actual ~150K, 10M causes RelayHub "Not enough gasleft()" revert)
    gas_limit = "500000"
    struct_hash = create_struct_hash(
        from_addr=eoa,
        to_addr=PROXY_FACTORY,
        data=proxy_data,
        tx_fee="0",
        gas_price="0",
        gas_limit=gas_limit,
        nonce=str(relay_nonce),
        relay_hub=RELAY_HUB,
        relay_addr=relay_address
    )
    print(f"\n  Struct hash: 0x{struct_hash.hex()}")
    
    # Sign
    signature = sign_struct_hash(struct_hash, PRIVATE_KEY)
    print(f"  Signature: 0x{signature[:20]}...")
    
    # Build request
    proxy_wallet = derived_proxy if derived_proxy.lower() == FUNDER.lower() else FUNDER
    
    request_body = {
        "from": eoa,
        "to": PROXY_FACTORY,
        "proxyWallet": proxy_wallet,
        "data": proxy_data,
        "nonce": relay_nonce,
        "signature": '0x' + signature if not signature.startswith('0x') else signature,
        "signatureParams": {
            "gasPrice": "0",
            "gasLimit": "500000",
            "relayerFee": "0",
            "relayHub": RELAY_HUB,
            "relay": relay_address,
        },
        "type": "PROXY",
        "metadata": "Redeem OXY + DASH positions",
    }
    
    # Submit transaction
    submit_path = '/submit'
    body_str = json.dumps(request_body)
    submit_headers = get_builder_headers(api_key, api_secret, api_passphrase, 'POST', submit_path, body_str)
    
    print(f"\n📤 Submitting proxy transaction to relayer...")
    r2 = requests.post(f'{RELAYER_URL}{submit_path}', headers=submit_headers, data=body_str, timeout=30)
    print(f"  Status: {r2.status_code}")
    print(f"  Response: {r2.text[:500]}")
    
    if r2.status_code != 200:
        print("❌ Failed to submit transaction")
        # Try individual redemptions
        print("\n🔄 Trying OXY only...")
        oxy_only_txns = [(1, Web3.to_checksum_address(CTF_CONTRACT), 0, bytes.fromhex(oxy_redeem_data[2:]))]
        oxy_proxy_data = encode_proxy_calldata(oxy_only_txns)
        
        struct_hash2 = create_struct_hash(eoa, PROXY_FACTORY, oxy_proxy_data, "0", "0", "500000", str(relay_nonce), RELAY_HUB, relay_address)
        signature2 = sign_struct_hash(struct_hash2, PRIVATE_KEY)
        
        request_body2 = {
            "from": eoa,
            "to": PROXY_FACTORY,
            "proxyWallet": proxy_wallet,
            "data": oxy_proxy_data,
            "nonce": relay_nonce,
            "signature": '0x' + signature2 if not signature2.startswith('0x') else signature2,
            "signatureParams": {
                "gasPrice": "0",
                "gasLimit": "500000",
                "relayerFee": "0",
                "relayHub": RELAY_HUB,
                "relay": relay_address,
            },
            "type": "PROXY",
            "metadata": "Redeem OXY position",
        }
        
        body_str2 = json.dumps(request_body2)
        submit_headers2 = get_builder_headers(api_key, api_secret, api_passphrase, 'POST', submit_path, body_str2)
        r3 = requests.post(f'{RELAYER_URL}{submit_path}', headers=submit_headers2, data=body_str2, timeout=30)
        print(f"  Status: {r3.status_code}")
        print(f"  Response: {r3.text[:500]}")
        
        if r3.status_code != 200:
            return False
        
        tx_response = r3.json()
    else:
        tx_response = r2.json()
    
    tx_id = tx_response.get('transactionID', '')
    tx_hash = tx_response.get('transactionHash', '')
    print(f"\n✅ Transaction submitted!")
    print(f"  TX ID: {tx_id}")
    print(f"  TX Hash: {tx_hash}")
    
    # Poll for completion
    print("\n⏳ Waiting for confirmation...")
    for i in range(20):
        time.sleep(3)
        try:
            poll_path = f'/transactions?id={tx_id}'
            poll_headers = get_builder_headers(api_key, api_secret, api_passphrase, 'GET', poll_path)
            r_poll = requests.get(f'{RELAYER_URL}{poll_path}', headers=poll_headers, timeout=10)
            if r_poll.ok:
                tx_data = r_poll.json()
                if isinstance(tx_data, list) and len(tx_data) > 0:
                    state = tx_data[0].get('state', '')
                    tx_hash = tx_data[0].get('transactionHash', tx_hash)
                    print(f"  Poll {i+1}: state={state}, hash={tx_hash}")
                    if state in ['CONFIRMED', 'MINED']:
                        print(f"✅ Transaction confirmed!")
                        break
                    if state in ['FAILED', 'REVERTED']:
                        print(f"❌ Transaction failed: {state}")
                        break
                else:
                    print(f"  Poll {i+1}: {r_poll.text[:100]}")
        except Exception as e:
            print(f"  Poll {i+1}: error - {e}")
    
    # Check post-redemption balance
    time.sleep(5)
    ba2 = clob.get_balance_allowance(BalanceAllowanceParams(asset_type='COLLATERAL', signature_type=1))
    usdc_after = int(ba2['balance']) / 1e6
    print(f"\n💰 USDC after: ${usdc_after:.6f}")
    print(f"  Gained: ${usdc_after - usdc_before:.6f}")
    
    oxy_after = clob.get_balance_allowance(BalanceAllowanceParams(
        asset_type='CONDITIONAL',
        token_id='99206042732582327149752420430322189152998581132203828422722078323956048439548',
        signature_type=1
    ))
    print(f"  OXY YES remaining: {int(oxy_after['balance'])/1e6}")
    
    return usdc_after > usdc_before


if __name__ == '__main__':
    success = main()
    if success:
        print("\n🎉 Redemption successful!")
    else:
        print("\n❌ Redemption failed or no balance change")
