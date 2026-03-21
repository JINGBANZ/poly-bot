"""conftest.py for bot/tests — CI environment stubs.

py_builder_signing_sdk is a prod-only dependency not installed in CI.
Stub it before test collection so test_redeemer.py imports cleanly.
All tests that exercise SDK code mock it at the call site anyway.
"""

import sys
from unittest.mock import MagicMock

for _mod in (
    "py_builder_signing_sdk",
    "py_builder_signing_sdk.config",
    "py_builder_signing_sdk.sdk_types",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
