"""Web3.py middleware for Ledger devices."""

from typing import Any, cast

# Some of the following imports utilize web3.py deps that are not deps of
# ledgereth.
from eth_account.messages import encode_typed_data
from eth_utils.hexadecimal import decode_hex
from web3.middleware import Web3Middleware
from web3.types import MakeRequestFn, RPCEndpoint, RPCResponse

from ledgereth.accounts import find_account, get_accounts
from ledgereth.messages import sign_message, sign_typed_data_draft
from ledgereth.transactions import create_transaction
from ledgereth.utils import decode_web3_access_list

"""
ACCOUNT DERIVATION ISSUES

Derivation path debate is ever ongoing.  Ledger Live app uses opton #3 here.
The old chrome app used #4.

1) 44'/60'/0'/0/x
2) 44'/60'/0'/x/0
3) 44'/60'/x'/0/0
4) 44'/60'/0'/x

The Ledger Live account enumeration algo appears to be:

1) Try legacy account X (if no balance, GOTO 3)
2) X+1 and GOTO 1
3) Try new-derivation Y (if no balance, RETURN)
4) Y+1 and GOTO 3

Since this library is trying not to resort to JSON-RPC calls, this algorithm is
not usable, so it's either or, and it currently defaults to the newer
derivation.

To use legacy derivation, set the environment variable LEDGER_LEGACY_ACCOUNTS

Ref (cannot find an authoritative source):
https://github.com/ethereum/EIPs/issues/84#issuecomment-292324521
"""


class AccountNotFoundError(ValueError):
    """An account with the given address was not found on the Ledger device.

    .. warning:: This might raise if the account is not found within
        ``MAX_ACCOUNTS_FETCH`` iterations on the derivation path.
    """

    pass


def _make_response(result: Any) -> RPCResponse:
    return {
        "jsonrpc": "2.0",
        "id": 1337,
        "result": result,
    }


class LedgerSignerMiddleware(Web3Middleware):
    """Web3.py middleware to leverage the Ledger device as a signer.

    It will automatically intercept the relevant JSON-RPC calls and respond with data
    from your Ledger device.

    :Intercepted JSON-RPC methods:

    - ``eth_sendTransaction`` (``web3.eth.send_transaction()``)
    - ``eth_accounts`` (``web3.eth.accounts``)
    - ``eth_sign`` (``web3.eth.sign()``)
    - ``eth_signTypedData``  (``web3.eth.sign_typed_data()``)

    :Example:

    .. code:: python

        >>> from web3.auto import w3
        >>> from ledgereth.web3 import LedgerSignerMiddleware
        >>> w3.middleware_onion.add(LedgerSignerMiddleware)
        >>> w3.eth.accounts
        ['0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266', '0x8C8d35429F74ec245F8Ef2f4Fd1e551cFF97d650', '0x98e503f35D0a019cB0a251aD243a4cCFCF371F46']

    """  # noqa: E501

    _dongle = None

    def wrap_make_request(self, make_request: MakeRequestFn):
        """Intercept some JSON-RPC requests and forward them to the Ledger device."""

        def middleware(method: RPCEndpoint, params: Any) -> RPCResponse:
            if method == "eth_sendTransaction":
                return self._handle_eth_send_transaction(params, make_request)
            elif method == "eth_accounts":
                return self._handle_eth_accounts(params)
            elif method == "eth_sign":
                return self._handle_eth_sign(params)
            elif method == "eth_signTypedData":
                return self._handle_eth_sign_typed_data(params)

            # Send on to the next middleware(s)
            return make_request(method, params)

        return middleware

    def _handle_eth_accounts(self, _: Any) -> RPCResponse:
        """Handler for eth_accounts RPC calls."""
        return _make_response(
            list(map(lambda a: a.address, get_accounts(dongle=self._dongle)))
        )

    def _handle_eth_send_transaction(
        self, params: Any, make_request: MakeRequestFn
    ) -> RPCResponse:
        """Handler for eth_sendTransaction RPC calls."""
        new_params = []

        for tx_obj in params:
            sender_address = tx_obj.get("from")
            nonce = tx_obj.get("nonce")
            gas = tx_obj.get("gas")
            gas_price = tx_obj.get("gasPrice")
            max_fee_per_gas = tx_obj.get("maxFeePerGas")
            max_priority_fee_per_gas = tx_obj.get("maxPriorityFeePerGas")
            value = tx_obj.get("value", "0x00")
            access_list = None

            if not sender_address:
                # TODO: Should this use a default?
                raise ValueError('"from" field not provided')

            if not gas:
                # TODO: What's the default web3.py behavior for this?
                raise ValueError('"gas" field not provided')

            if not gas_price and not max_fee_per_gas:
                raise ValueError('"gasPrice" or "maxFeePerGas" field not provided')

            sender_account = find_account(sender_address, dongle=self._dongle)

            if not sender_account:
                raise AccountNotFoundError(f"Account {sender_address} not found")

            if nonce is None:
                nonce = self._w3.eth.get_transaction_count(sender_address)

            if "accessList" in tx_obj:
                access_list = decode_web3_access_list(tx_obj["accessList"])

            chain_id = self._w3.eth.chain_id

            if not chain_id:
                raise ValueError("No chain ID found?")

            # NOTE: web3.py's weird async typing
            # TODO: Support web3.py's async providers
            assert isinstance(nonce, int)

            signed_tx = create_transaction(
                # TODO: web3.py typing suggests this could be corotine?
                chain_id=cast(int, chain_id),
                destination=tx_obj.get("to"),
                amount=int(value, 16),
                gas=int(gas, 16),
                gas_price=int(gas_price, 16) if gas_price else None,
                max_fee_per_gas=int(max_fee_per_gas, 16) if max_fee_per_gas else None,
                max_priority_fee_per_gas=(
                    int(max_priority_fee_per_gas, 16)
                    if max_priority_fee_per_gas
                    else None
                ),
                nonce=nonce,
                data=tx_obj.get("data", b""),
                sender_path=sender_account.path,
                access_list=access_list,
                dongle=self._dongle,
            )

            new_params.append(signed_tx.rawTransaction)

        # Change to raw tx call
        method = cast(RPCEndpoint, "eth_sendRawTransaction")
        params = new_params

        return make_request(method, params)

    def _handle_eth_sign(self, params: Any) -> RPCResponse:
        """Handler for eth_sign RPC calls."""
        if len(params) != 2:
            raise ValueError("Unexpected RPC request params length for eth_sign")

        account = params[0]
        message = decode_hex(params[1])

        signer_account = find_account(account, dongle=self._dongle)

        if not signer_account:
            raise AccountNotFoundError(f"Account {account} not found")

        signed = sign_message(message, signer_account.path, dongle=self._dongle)

        return _make_response(signed.signature)

    def _handle_eth_sign_typed_data(self, params: Any) -> RPCResponse:
        """Handler for eth_signTypedData RPC calls."""
        if len(params) != 2:
            raise ValueError("Unexpected RPC request params length for eth_sign")

        account = params[0]
        typed_data = params[1]

        if not isinstance(typed_data, dict):
            raise TypeError(
                "Expected type data to be a dictionary for second param for "
                "eth_signTypedData call"
            )

        # Use eth_account to encode and hash the typed data
        signable = encode_typed_data(full_message=typed_data)
        domain_hash = signable.header
        message_hash = signable.body

        # Find the account and sign with Ledger
        signer_account = find_account(account, dongle=self._dongle)

        if not signer_account:
            raise AccountNotFoundError(f"Account {account} not found on Ledger device.")

        signed = sign_typed_data_draft(
            domain_hash, message_hash, signer_account.path, dongle=self._dongle
        )

        return _make_response(signed.signature)
