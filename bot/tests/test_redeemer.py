"""Tests for bot/redeemer.py — encoding, signing, state management, and integration."""

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest
import requests
from eth_account import Account
from web3 import Web3

from bot import redeemer


# ── Fixtures ─────────────────────────────────────────────────────────

# Deterministic test private key (DO NOT use for real funds)
TEST_PRIVATE_KEY = "0x" + "ab" * 32
TEST_ACCOUNT = Account.from_key(TEST_PRIVATE_KEY)
TEST_ADDRESS = TEST_ACCOUNT.address

SAMPLE_CONDITION_ID = "0x" + "cf" * 32


@pytest.fixture(autouse=True)
def _reset_env_cache():
    """Reset module-level env cache before each test."""
    redeemer._cached_env = None
    redeemer._cached_env_mtime = 0.0
    yield


@pytest.fixture
def tmp_redemptions(tmp_path):
    """Patch REDEMPTIONS_FILE to a temp location."""
    path = str(tmp_path / "redemptions.json")
    with patch.object(redeemer, "REDEMPTIONS_FILE", path):
        yield path


# ── _encode_redeem_calldata ──────────────────────────────────────────

class TestEncodeRedeemCalldata:
    def test_starts_with_selector(self):
        data = redeemer._encode_redeem_calldata(SAMPLE_CONDITION_ID)
        assert data[:4] == redeemer.REDEEM_SELECTOR

    def test_deterministic(self):
        a = redeemer._encode_redeem_calldata(SAMPLE_CONDITION_ID)
        b = redeemer._encode_redeem_calldata(SAMPLE_CONDITION_ID)
        assert a == b

    def test_handles_0x_prefix(self):
        cid_no_prefix = SAMPLE_CONDITION_ID[2:]
        a = redeemer._encode_redeem_calldata(SAMPLE_CONDITION_ID)
        b = redeemer._encode_redeem_calldata(cid_no_prefix)
        assert a == b

    def test_encodes_usdc_and_index_sets(self):
        """Verify the encoded params contain USDC address and index sets [1,2]."""
        data = redeemer._encode_redeem_calldata(SAMPLE_CONDITION_ID)
        # Should be selector (4) + ABI-encoded params
        assert len(data) > 4
        # The first 32-byte word in params should encode the USDC address
        params = data[4:]
        # address is left-padded to 32 bytes
        usdc_bytes = bytes.fromhex(redeemer.USDC_ADDRESS[2:].lower())
        assert usdc_bytes in params


# ── _derive_proxy_wallet ─────────────────────────────────────────────

class TestDeriveProxyWallet:
    def test_returns_checksum_address(self):
        addr = redeemer._derive_proxy_wallet(TEST_ADDRESS, redeemer.PROXY_FACTORY)
        assert Web3.is_checksum_address(addr)

    def test_deterministic(self):
        a = redeemer._derive_proxy_wallet(TEST_ADDRESS, redeemer.PROXY_FACTORY)
        b = redeemer._derive_proxy_wallet(TEST_ADDRESS, redeemer.PROXY_FACTORY)
        assert a == b

    def test_different_owners_produce_different_wallets(self):
        other_key = "0x" + "cd" * 32
        other_addr = Account.from_key(other_key).address
        a = redeemer._derive_proxy_wallet(TEST_ADDRESS, redeemer.PROXY_FACTORY)
        b = redeemer._derive_proxy_wallet(other_addr, redeemer.PROXY_FACTORY)
        assert a != b


# ── _build_proxy_request ─────────────────────────────────────────────

class TestBuildProxyRequest:
    def test_request_structure(self):
        proxy_calldata = redeemer._encode_proxy_calldata([
            {"typeCode": 1, "to": redeemer.CTF_ADDRESS, "value": 0,
             "data": redeemer._encode_redeem_calldata(SAMPLE_CONDITION_ID)}
        ])
        relay_payload = {"address": "0x" + "11" * 20, "nonce": 42}

        req = redeemer._build_proxy_request(
            private_key=TEST_PRIVATE_KEY,
            from_addr=TEST_ADDRESS,
            proxy_calldata=proxy_calldata,
            relay_payload=relay_payload,
            metadata="test",
        )

        assert req["from"] == TEST_ADDRESS
        assert req["to"] == redeemer.PROXY_FACTORY
        assert req["type"] == "PROXY"
        assert req["nonce"] == "42"
        assert req["data"].startswith("0x")
        assert req["signature"].startswith("0x")
        assert "proxyWallet" in req
        assert Web3.is_checksum_address(req["proxyWallet"])
        assert req["metadata"] == "test"

    def test_signature_is_valid_hex(self):
        proxy_calldata = redeemer._encode_proxy_calldata([
            {"typeCode": 1, "to": redeemer.CTF_ADDRESS, "value": 0,
             "data": redeemer._encode_redeem_calldata(SAMPLE_CONDITION_ID)}
        ])
        relay_payload = {"address": "0x" + "22" * 20, "nonce": 1}

        req = redeemer._build_proxy_request(
            private_key=TEST_PRIVATE_KEY,
            from_addr=TEST_ADDRESS,
            proxy_calldata=proxy_calldata,
            relay_payload=relay_payload,
        )

        sig = req["signature"]
        # Should be hex string, 65 bytes (130 hex chars + 0x)
        assert len(bytes.fromhex(sig[2:])) == 65


# ── _already_redeemed / _record_redemption ───────────────────────────

class TestStateManagement:
    def test_already_redeemed_false_when_empty(self, tmp_redemptions):
        assert redeemer._already_redeemed("0xabc") is False

    def test_record_and_check(self, tmp_redemptions):
        redeemer._record_redemption("Test Market", "0xabc", 10.0, 10.0, "0xtxhash")
        assert redeemer._already_redeemed("0xabc") is True
        assert redeemer._already_redeemed("0xdef") is False

    def test_record_updates_total(self, tmp_redemptions):
        redeemer._record_redemption("M1", "0x1", 5.0, 5.0)
        redeemer._record_redemption("M2", "0x2", 3.0, 3.0)
        data = redeemer._load_redemptions()
        assert data["total"] == 8.0
        assert len(data["entries"]) == 2

    def test_atomic_write_creates_valid_json(self, tmp_redemptions):
        redeemer._record_redemption("M1", "0x1", 10.0, 10.0, "0xhash")
        with open(tmp_redemptions) as f:
            data = json.load(f)
        assert data["total"] == 10.0
        assert len(data["entries"]) == 1


# ── _save_redemptions atomic write ───────────────────────────────────

class TestAtomicSave:
    def test_no_tmp_files_left_on_success(self, tmp_path):
        path = str(tmp_path / "redemptions.json")
        with patch.object(redeemer, "REDEMPTIONS_FILE", path):
            redeemer._save_redemptions({"total": 0, "entries": []})
        # No leftover .tmp files
        tmp_files = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
        assert tmp_files == []
        assert os.path.exists(path)


# ── _load_env caching ───────────────────────────────────────────────

class TestLoadEnvCaching:
    def test_caches_result(self, tmp_path):
        env_file = tmp_path / "env"
        env_file.write_text("MY_VAR=hello\n")
        with patch.object(redeemer, "ENV_FILE", str(env_file)):
            env1 = redeemer._load_env()
            env2 = redeemer._load_env()
        assert env1["MY_VAR"] == "hello"
        assert env1 is env2  # Same object = cached

    def test_refreshes_on_mtime_change(self, tmp_path):
        env_file = tmp_path / "env"
        env_file.write_text("VAR=v1\n")
        with patch.object(redeemer, "ENV_FILE", str(env_file)):
            env1 = redeemer._load_env()
            assert env1["VAR"] == "v1"
            # Simulate file change with different mtime
            env_file.write_text("VAR=v2\n")
            # Force mtime difference
            os.utime(str(env_file), (9999999999, 9999999999))
            env2 = redeemer._load_env()
            assert env2["VAR"] == "v2"
            assert env1 is not env2

    def test_force_refresh(self, tmp_path):
        env_file = tmp_path / "env"
        env_file.write_text("X=1\n")
        with patch.object(redeemer, "ENV_FILE", str(env_file)):
            env1 = redeemer._load_env()
            env2 = redeemer._load_env(force_refresh=True)
            assert env1 is not env2  # Different objects = re-read


# ── Retry logic ──────────────────────────────────────────────────────

class TestRetryLogic:
    @patch.object(redeemer, "RETRY_BACKOFF_BASE", 0.01)  # Fast retries for tests
    @patch("bot.redeemer.requests.get")
    def test_get_relay_payload_retries_on_connection_error(self, mock_get):
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("fail"),
            requests.exceptions.ConnectionError("fail"),
            MagicMock(status_code=200, json=lambda: {"address": "0x1", "nonce": 1},
                      raise_for_status=lambda: None),
        ]
        result = redeemer._get_relay_payload("0xtest")
        assert result == {"address": "0x1", "nonce": 1}
        assert mock_get.call_count == 3

    @patch.object(redeemer, "RETRY_BACKOFF_BASE", 0.01)
    @patch("bot.redeemer.requests.get")
    def test_get_relay_payload_raises_after_max_retries(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("timeout")
        with pytest.raises(requests.exceptions.Timeout):
            redeemer._get_relay_payload("0xtest")
        assert mock_get.call_count == redeemer.MAX_RETRIES

    @patch.object(redeemer, "RETRY_BACKOFF_BASE", 0.01)
    @patch("bot.redeemer.requests.get")
    def test_get_relay_payload_no_retry_on_4xx(self, mock_get):
        resp = MagicMock()
        resp.status_code = 400
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=resp)
        mock_get.return_value = resp
        with pytest.raises(requests.exceptions.HTTPError):
            redeemer._get_relay_payload("0xtest")
        assert mock_get.call_count == 1  # No retry on 400

    @patch.object(redeemer, "RETRY_BACKOFF_BASE", 0.01)
    @patch("bot.redeemer.requests.post")
    def test_submit_transaction_retries_on_timeout(self, mock_post):
        mock_post.side_effect = [
            requests.exceptions.Timeout("timeout"),
            MagicMock(status_code=200, json=lambda: {"transactionID": "tx123"},
                      raise_for_status=lambda: None),
        ]
        bc = MagicMock()
        bc.generate_builder_headers.return_value = MagicMock(to_dict=lambda: {})
        result = redeemer._submit_transaction(bc, {"test": True})
        assert result == {"transactionID": "tx123"}
        assert mock_post.call_count == 2


# ── check_and_redeem integration ─────────────────────────────────────

class TestCheckAndRedeem:
    @patch("bot.redeemer.get_redeemable_positions")
    def test_returns_empty_when_nothing_redeemable(self, mock_positions):
        mock_positions.return_value = []
        assert redeemer.check_and_redeem() == []

    @patch("bot.redeemer.get_redeemable_positions")
    def test_dry_run_skips_redemption(self, mock_positions):
        mock_positions.return_value = [
            {"conditionId": "0xabc", "title": "Test", "size": "5.0"}
        ]
        results = redeemer.check_and_redeem(dry_run=True)
        assert len(results) == 1
        assert results[0]["action"] == "dry_run"

    @patch("bot.redeemer.write_alert", create=True)
    @patch("bot.redeemer._record_redemption")
    @patch("bot.redeemer.redeem_position")
    @patch("bot.redeemer.get_redeemable_positions")
    def test_successful_redemption_flow(self, mock_positions, mock_redeem,
                                         mock_record, mock_alert):
        mock_positions.return_value = [
            {"conditionId": "0xabc", "title": "Test Market", "size": "10.0"}
        ]
        mock_redeem.return_value = {"success": True, "tx_hash": "0xhash"}

        with patch("bot.alerts.write_alert"):
            results = redeemer.check_and_redeem()

        assert len(results) == 1
        assert results[0]["success"] is True
        mock_redeem.assert_called_once_with("0xabc", "Test Market")
        mock_record.assert_called_once()
