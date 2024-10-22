"""Constanes used by the ledgereth package."""

import os
from typing import Any


def getenvint(key, default=0):
    """Get an int from en env var or use default."""
    try:
        return int(os.environ["MAX_ACCOUNTS_FETCH"])
    except (KeyError, TypeError):
        return default


# Chain ID to use if not given by user
DEFAULT_CHAIN_ID = 1

# Default accounts to fetch with get_accounts
DEFAULT_ACCOUNTS_FETCH = 3

# Number of accounts to fetch when looking up an account by address
MAX_ACCOUNTS_FETCH = getenvint("MAX_ACCOUNTS_FETCH", 5)

# Whether to use the legacy bip32 path derivation used by Ledger Chrome app
LEGACY_ACCOUNTS = os.getenv("LEDGER_LEGACY_ACCOUNTS") is not None

DEFAULT_PATH_STRING = "44'/60'/0'/0/0"
DEFAULT_PATH_ENCODED = (
    b"\x80\x00\x00,\x80\x00\x00<\x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
)
if LEGACY_ACCOUNTS:
    DEFAULT_PATH_STRING = "44'/60'/0'/0"
    DEFAULT_PATH_ENCODED = b"\x80\x00\x00,\x80\x00\x00<\x80\x00\x00\x00\x00\x00\x00\x00"
DEFAULT_PATH = DEFAULT_PATH_ENCODED.hex()
VRS_RETURN_LENGTH = (65).to_bytes(1, "big")

# Data size expected from Ledger
DATA_CHUNK_SIZE = 255

# Default "zero" values in EVM/Solidity
DEFAULTS: dict[type, Any] = {
    int: 0,
    bytes: b"",
}
