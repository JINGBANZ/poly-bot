"""Tests for centralized .env loading (bot/config.py load_env_file)."""

import os

import pytest

from bot import config


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# comment line\n"
        "\n"
        "POLY_TEST_KEY_A=value-a\n"
        "POLY_TEST_KEY_B = spaced value \n"
        'POLY_TEST_KEY_C="quoted"\n'
        "not a kv line\n"
    )
    return str(p)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("POLY_TEST_KEY_A", "POLY_TEST_KEY_B", "POLY_TEST_KEY_C"):
        monkeypatch.delenv(k, raising=False)


def test_loads_keys_and_strips(env_file):
    n = config.load_env_file(env_file)
    assert n == 3
    assert os.environ["POLY_TEST_KEY_A"] == "value-a"
    assert os.environ["POLY_TEST_KEY_B"] == "spaced value"
    assert os.environ["POLY_TEST_KEY_C"] == "quoted"


def test_existing_env_vars_win(env_file, monkeypatch):
    monkeypatch.setenv("POLY_TEST_KEY_A", "from-real-env")
    config.load_env_file(env_file)
    assert os.environ["POLY_TEST_KEY_A"] == "from-real-env"


def test_missing_file_is_noop():
    assert config.load_env_file("/nonexistent/.env") == 0


def test_env_file_default_is_repo_root():
    assert config.ENV_FILE.endswith(".env")
    assert os.path.dirname(config.ENV_FILE) == config.BASE_DIR or \
        os.environ.get("POLY_BOT_ENV_FILE")
