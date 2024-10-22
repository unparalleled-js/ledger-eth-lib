"""ledgereth Objects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import IntEnum
from typing import Any

from eth_utils.address import to_checksum_address
from eth_utils.hexadecimal import encode_hex
from rlp import Serializable, decode, encode
from rlp.sedes import BigEndianInt, Binary, CountableList, big_endian_int, binary
from rlp.sedes import List as ListSedes

from ledgereth.constants import DEFAULT_CHAIN_ID
from ledgereth.utils import (
    coerce_list_types,
    is_bip32_path,
    is_bytes,
    is_optional_bytes,
    parse_bip32_path,
)

address = Binary.fixed_length(20, allow_empty=False)
address_allow_empty = Binary.fixed_length(20, allow_empty=True)
access_list_sede_type = CountableList(
    ListSedes(
        [
            address,
            CountableList(BigEndianInt(32)),
        ]
    ),
)
RPC_TX_PROP_TRANSLATION = {
    "gas_price": "gasPrice",
    "gas_limit": "gas",
    "amount": "value",
    "destination": "to",
    "max_priority_fee_per_gas": "maxPriorityFeePerGas",
    "max_fee_per_gas": "maxFeePerGas",
    "access_list": "accessList",
    "chain_id": "chainId",
}
RPC_TX_PROPS = [
    "chainId",
    "from",
    "to",
    "gas",
    "gasPrice",
    "value",
    "data",
    "nonce",
    "maxFeePerGas",
    "maxPriorityFeePerGas",
    "chainId",
    "accessList",
]
MAX_LEGACY_CHAIN_ID = 0xFFFFFFFF + 1
MAX_CHAIN_ID = 0x38D7EA4C67FFF


class TransactionType(IntEnum):
    """An Ethereum EIP-2718 transaction type."""

    #: Original and EIP-155
    LEGACY = 0
    #: Type-1 (Access Lists)
    EIP_2930 = 1
    #: Type-2 (Transaction fee change to max fee and priority fee)
    EIP_1559 = 2

    def to_byte(self):
        """Decode TransactionType to a single byte."""
        return self.value.to_bytes(1, "big")


class ISO7816Command:
    """An ISO-7816 APDU Command binary to be sent to the Ledger device."""

    def __init__(
        self,
        CLA: bytes,  # noqa: N803
        INS: bytes,  # noqa: N803
        P1: bytes,  # noqa: N803
        P2: bytes,  # noqa: N803
        Lc: bytes | None = None,  # noqa: N803
        Le: bytes | None = None,  # noqa: N803
        data: bytes | None = None,
    ):
        """Initialize an ISO-7816 Command."""
        if not (
            is_bytes(CLA)
            and is_bytes(INS)
            and is_bytes(P1)
            and is_bytes(P2)
            and is_optional_bytes(Lc)
            and is_optional_bytes(Le)
            and is_optional_bytes(data)
        ):
            raise TypeError("Command parts must be type bytes")

        self.CLA = CLA
        self.INS = INS
        self.P1 = P1
        self.P2 = P2
        self.Lc = Lc or b"\x00" if not data else len(data).to_bytes(1, "big")
        self.Le = Le
        self.data = data

    def set_data(self, data: bytes, Lc: bytes | None = None) -> None:  # noqa: N803
        """Set the command data and its length.

        :param data: (:class:`bytes`) - The raw ``bytes`` data. This should not
            exceed the max chunk length of 255 (including command data)
        :param Lc: (:class:`bytes`) - The length of the data
        """
        self.data = data

        if len(self.data) > 255:
            # TODO: Warning?
            return

        if Lc is None:
            self.Lc = len(self.data).to_bytes(1, "big")
        else:
            self.Lc = Lc

    def encode(self) -> bytes:
        """Encode the command into ``bytes`` to be sent to the Ledger device.

        :return: Encoded ``bytes`` data
        """
        encoded = self.CLA + self.INS + self.P1 + self.P2

        if self.data is not None:
            if self.Lc is None:
                self.Lc = (len(self.data)).to_bytes(1, "big")
            encoded += self.Lc
            encoded += self.data
        else:
            encoded += self.Lc

        if self.Le is not None:
            encoded += self.Le

        return encoded

    def encode_hex(self) -> str:
        """Encode the command into hex bytes representation.

        :return: Encoded hex ``str``
        """
        return self.encode().hex()


class LedgerAccount:
    """An account derived from the private key on a Ledger device."""

    #: The HD path of the account
    path: str

    #: The HD path of the account
    path_encoded: bytes

    #: The account's address
    address: str

    def __init__(self, path, address):
        """Initialize an account.

        :param path: (``str``) Derivation path for the account
        :param address: (``str``) Address of the account
        """
        if not is_bip32_path(path):
            raise ValueError("Invalid BIP32 Ethereum path")

        self.path = path
        self.path_encoded = parse_bip32_path(path)
        self.address = to_checksum_address(address)

    def __repr__(self):
        return f"<ledgereth.objects.LedgerAccount {self.address}>"

    def __eq__(self, other):
        if isinstance(other, LedgerAccount):
            return self.path == other.path and self.address == other.address
        return False

    def __hash__(self):
        return hash((self.path, self.address))


class SerializableTransaction(Serializable):
    """An RLP Serializable transaction object."""

    @classmethod
    @abstractmethod
    def from_rawtx(cls, rawtx: bytes) -> SerializableTransaction:
        """Instantiates a SerializableTransaction given a raw encoded transaction.

        :param rawtx: (:class:`bytes`) - The decoded raw transaction ``bytes``
            to encode into a :class:`ledgereth.objects.SerializableTransaction`
        :return: Instantiated :class:`ledgereth.objects.SerializableTransaction`
        """

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the transaction.

        :return: Transaction dict
        """
        d = {}
        for name, _ in self.__class__._meta.fields:
            d[name] = getattr(self, name)
        return d

    def to_rpc_dict(self) -> dict[str, Any]:
        """To a dict compatible with web3.py or JSON-RPC.

        :return: Transaction dict
        """
        d: dict[str, Any] = {}

        for name, _ in self.__class__._meta.fields:
            key = (
                RPC_TX_PROP_TRANSLATION[name]
                if name in RPC_TX_PROP_TRANSLATION
                else name
            )

            if key in RPC_TX_PROPS:
                # Need to format an access list differently for web3/RPC-like
                # objects.  It expects a list of objects
                if key == "accessList":
                    orig = getattr(self, name)
                    d[key] = []
                    for item in orig:
                        d[key].append(
                            {
                                "address": item[0],
                                "storageKeys": [
                                    int.from_bytes(slot, "big") for slot in item[1]
                                ],
                            }
                        )
                else:
                    d[key] = getattr(self, name)

        return d


class Transaction(SerializableTransaction):
    """Unsigned legacy or `EIP-155`_ transaction.

    .. warning:: chain_id for type 0 ("Legacy") transactions must be less than
        4294967295, the largest 32-bit unsigned integer.

    .. note:: A chain_id is set by default (``1``).  It is not required to be
        a valid legacy transaction, but without it your transaction is
        suceptible to replay attack.  If for some reason you absolutely do not
        want it in your tx, set it to ``None``.

    .. _`EIP-155`: https://eips.ethereum.org/EIPS/eip-155
    """

    fields = [  # noqa: RUF012
        ("nonce", big_endian_int),
        ("gas_price", big_endian_int),
        ("gas_limit", big_endian_int),
        ("destination", address_allow_empty),
        ("amount", big_endian_int),
        ("data", binary),
        ("chain_id", big_endian_int),
        # Expected nine elements as part of EIP-155 transactions
        ("dummy1", big_endian_int),
        ("dummy2", big_endian_int),
    ]

    #: The EIP-2718 transaction type
    transaction_type = TransactionType.LEGACY

    def __init__(
        self,
        nonce: int,
        gas_price: int,
        gas_limit: int,
        destination: bytes,
        amount: int,
        data: bytes,
        chain_id: int = DEFAULT_CHAIN_ID,
        dummy1: int = 0,
        dummy2: int = 0,
    ):
        """Initialize an unsigned transaction.

        :param nonce: (``int``) Transaction nonce
        :param gas_price: (``int``) Gas price in wei
        :param gas_limit: (``int``) Gas limit
        :param destination: (``bytes``) Destination address
        :param amount: (``int``) Amount of Ether to send in wei
        :param data: (``bytes``) Transaction data
        :param chain_id: (``int``) Chain ID
        :param dummy1: (``int``) **DO NOT SET**
        :param dummy2: (``int``) **DO NOT SET**
        """
        if chain_id > MAX_LEGACY_CHAIN_ID:
            """Chain IDs above 32-bits seems to cause app-ethereum to create
            invalid signatures.  It's not yet clear why this is, or where the
            bug is, or even if it's a bug.  See the following issue for details:

            https://github.com/mikeshultz/ledger-eth-lib/issues/41
            """
            raise ValueError(
                "chain_id must be a 32-bit integer for type 0 transactions."
                " (See issue #41)"
            )

        super().__init__(
            nonce,
            gas_price,
            gas_limit,
            destination,
            amount,
            data,
            chain_id,
            dummy1,
            dummy2,
        )

    @classmethod
    def from_rawtx(cls, rawtx: bytes) -> Transaction:
        """Instantiate a Transaction object from a raw encoded transaction.

        :param rawtx: (``bytes``) A raw transaction to instantiate with
        :returns: :class:`ledgereth.objects.Transaction`
        """
        if rawtx[0] < 127:
            raise ValueError("Transaction is not a legacy transaction")

        return Transaction(
            *coerce_list_types(
                [int, int, int, bytes, int, bytes, int, int, int], decode(rawtx)
            )
        )


class Type1Transaction(SerializableTransaction):
    """An unsigned Type 1 transaction.

    .. warning:: chain_id for type 1 transactions must be less than 999999999999999,
        the largest unsigned integer that the device can render on-screen.

    Encoded tx format spec:

    .. code::

        0x01 || rlp([chainId, nonce, gasPrice, gasLimit, destination, amount, data, accessList])
    """  # noqa: E501

    # TODO: Fix this ruff error
    fields = [  # noqa: RUF012
        ("chain_id", big_endian_int),
        ("nonce", big_endian_int),
        ("gas_price", big_endian_int),
        ("gas_limit", big_endian_int),
        ("destination", address_allow_empty),
        ("amount", big_endian_int),
        ("data", binary),
        ("access_list", access_list_sede_type),
    ]

    #: The EIP-2718 transaction type
    transaction_type = TransactionType.EIP_2930

    def __init__(
        self,
        chain_id: int,
        nonce: int,
        gas_price: int,
        gas_limit: int,
        destination: bytes,
        amount: int,
        data: bytes,
        access_list: list[tuple[bytes, list[int]]] | None = None,
    ):
        """Initialize an unsigned type 2 transaction.

        :param chain_id: (``int``) Chain ID
        :param nonce: (``int``) Transaction nonce
        :param gas_price: (``int``) Gas price in wei
        :param gas_limit: (``int``) Gas limit
        :param destination: (``bytes``) Destination address
        :param amount: (``int``) Amount of Ether to send in wei
        :param data: (``bytes``) Transaction data
        :param access_list: (``list[tuple[bytes, list[int]]] | None``) EIP-2718 Access
            list
        """
        access_list = access_list or []

        if chain_id > MAX_CHAIN_ID:
            """Chain IDs above 999999999999999 cause app-ethereum to throw an error
            because its unable to render on the device.

            Ref: https://github.com/mikeshultz/ledger-eth-lib/issues/41
            Ref: https://github.com/LedgerHQ/app-ethereum/issues/283
            """
            raise ValueError(
                "chain_id must not be above 999999999999999. (See issue #41)"
            )

        super().__init__(
            chain_id,
            nonce,
            gas_price,
            gas_limit,
            destination,
            amount,
            data,
            access_list,
        )

    @classmethod
    def from_rawtx(cls, rawtx: bytes) -> Type1Transaction:
        """Instantiate a Type1Transaction object from a raw encoded transaction.

        :param rawtx: (``bytes``) A raw transaction to instantiate with
        :returns: :class:`ledgereth.objects.Type1Transaction`
        """
        if rawtx[0] != cls.transaction_type:
            raise ValueError(
                f"Transaction is not a type {cls.transaction_type} transaction"
            )

        return Type1Transaction(
            *coerce_list_types(
                [int, int, int, int, bytes, int, bytes, None],
                decode(rawtx[1:]),
            )
        )


class Type2Transaction(SerializableTransaction):
    """An unsigned Type 2 transaction.

    .. warning:: chain_id for type 2 transactions must be less than 999999999999999,
        the largest unsigned integer that the device can render on-screen.

    Encoded TX format spec:

    .. code::

        0x02 || rlp([chain_id, nonce, max_priority_fee_per_gas, max_fee_per_gas, gas_limit, destination, amount, data, access_list])
    """  # noqa: E501

    fields = [  # noqa: RUF012
        ("chain_id", big_endian_int),
        ("nonce", big_endian_int),
        ("max_priority_fee_per_gas", big_endian_int),
        ("max_fee_per_gas", big_endian_int),
        ("gas_limit", big_endian_int),
        ("destination", address_allow_empty),
        ("amount", big_endian_int),
        ("data", binary),
        ("access_list", access_list_sede_type),
    ]

    #: The EIP-2718 transaction type
    transaction_type = TransactionType.EIP_1559

    def __init__(
        self,
        chain_id: int,
        nonce: int,
        max_priority_fee_per_gas: int,
        max_fee_per_gas: int,
        gas_limit: int,
        destination: bytes,
        amount: int,
        data: bytes,
        access_list: list[tuple[bytes, list[int]]] | None = None,
    ):
        """Initialize an unsigned type 2 transaction.

        :param chain_id: (``int``) Chain ID
        :param nonce: (``int``) Transaction nonce
        :param max_priority_fee_per_gas: (``int``) Priority fee per gas (in
            wei) to provide to the miner of the block.
        :param max_fee_per_gas: (``int``) Maximum fee in wei to pay for the
            transaction.  This is not compatible with :code:`gas_price`.
        :param gas_limit: (``int``) Gas limit
        :param destination: (``bytes``) Destination address
        :param amount: (``int``) Amount of Ether to send in wei
        :param data: (``bytes``) Transaction data
        :param access_list: (``list[tuple[bytes, list[int]]]``) EIP-2718 Access
            list
        """
        access_list = access_list or []

        if chain_id > MAX_CHAIN_ID:
            """Chain IDs above 999999999999999 cause app-ethereum to throw an error
            because its unable to render on the device.

            Ref: https://github.com/mikeshultz/ledger-eth-lib/issues/41
            Ref: https://github.com/LedgerHQ/app-ethereum/issues/283
            """
            raise ValueError(
                "chain_id must not be above 999999999999999. (See issue #41)"
            )

        super().__init__(
            chain_id,
            nonce,
            max_priority_fee_per_gas,
            max_fee_per_gas,
            gas_limit,
            destination,
            amount,
            data,
            access_list,
        )

    @classmethod
    def from_rawtx(cls, rawtx: bytes) -> Type2Transaction:
        """Instantiate a Type2Transaction object from a raw encoded transaction.

        :param rawtx: (``bytes``) A raw transaction to instantiate with
        :returns: :class:`ledgereth.objects.Type2Transaction`
        """
        if rawtx[0] != cls.transaction_type:
            raise ValueError(
                f"Transaction is not a type {cls.transaction_type} transaction"
            )

        return Type2Transaction(
            *coerce_list_types(
                [int, int, int, int, int, bytes, int, bytes, None],
                decode(rawtx[1:]),
            )
        )


class SignedTransaction(SerializableTransaction):
    """Signed legacy or EIP-155 transaction."""

    fields = [  # noqa: RUF012
        ("nonce", big_endian_int),
        ("gas_price", big_endian_int),
        ("gas_limit", big_endian_int),
        ("destination", address_allow_empty),
        ("amount", big_endian_int),
        ("data", binary),
        ("v", big_endian_int),
        ("r", big_endian_int),
        ("s", big_endian_int),
    ]

    #: The EIP-2718 transaction type
    transaction_type = TransactionType.LEGACY

    def __init__(
        self,
        nonce: int,
        gas_price: int,
        gas_limit: int,
        destination: bytes,
        amount: int,
        data: bytes,
        v: int,
        r: int,
        s: int,
    ):
        """Initialize an unsigned transaction.

        :param nonce: (``int``) Transaction nonce
        :param gas_price: (``int``) Gas price in wei
        :param gas_limit: (``int``) Gas limit
        :param destination: (``bytes``) Destination address
        :param amount: (``int``) Amount of Ether to send in wei
        :param data: (``bytes``) Transaction data
        :param v: (``int``) Signature v value
        :param r: (``int``) Signature r value
        :param s: (``int``) Signature s value
        """
        super().__init__(
            nonce, gas_price, gas_limit, destination, amount, data, v, r, s
        )

    @classmethod
    def from_rawtx(cls, rawtx: bytes) -> SignedTransaction:
        """Instantiate a SignedTransaction object from a raw encoded transaction.

        :param rawtx: (``bytes``) A raw signed transaction to instantiate with
        :returns: :class:`ledgereth.objects.SignedTransaction`
        """
        if rawtx[0] < 127:
            raise ValueError("Transaction is not a legacy transaction")

        return SignedTransaction(
            *coerce_list_types([int, int, int, int, bytes, int, bytes], decode(rawtx))
        )

    def raw_transaction(self):
        """Return an encoded raw signed transaction.

        Encoded signed TX format spec:

        .. code::

            rlp([nonce, gasPrice, gasLimit, destination, amount, data, signatureV, signatureR, signatureS])

        :returns: Encoded raw signed transaction bytes
        """  # noqa: E501
        return encode_hex(encode(self, SignedTransaction))

    # Match the API of the web3.py Transaction object
    #: Encoded raw signed transaction
    rawTransaction = property(raw_transaction)  # noqa: N815


class SignedType1Transaction(SerializableTransaction):
    """A signed Type 1 transaction."""

    fields = [  # noqa: RUF012
        ("chain_id", big_endian_int),
        ("nonce", big_endian_int),
        ("gas_price", big_endian_int),
        ("gas_limit", big_endian_int),
        ("destination", address_allow_empty),
        ("amount", big_endian_int),
        ("data", binary),
        ("access_list", access_list_sede_type),
        ("y_parity", big_endian_int),
        ("sender_r", big_endian_int),
        ("sender_s", big_endian_int),
    ]

    #: The EIP-2718 transaction type
    transaction_type = TransactionType.EIP_2930

    def __init__(
        self,
        chain_id: int,
        nonce: int,
        gas_price: int,
        gas_limit: int,
        destination: bytes,
        amount: int,
        data: bytes,
        access_list: list[tuple[bytes, list[int]]],
        y_parity: int,
        sender_r: int,
        sender_s: int,
    ):
        """Initialize a signed type 1 transaction.

        :param chain_id: (``int``) Chain ID
        :param nonce: (``int``) Transaction nonce
        :param gas_price: (``int``) Gas price in wei
        :param gas_limit: (``int``) Gas limit
        :param destination: (``bytes``) Destination address
        :param amount: (``int``) Amount of Ether to send in wei
        :param data: (``bytes``) Transaction data
        :param access_list: (``list[tuple[bytes, list[int]]]``) EIP-2718 Access
            list
        :param y_parity: (``int``) Parity byte for the signature
        :param sender_r: (``int``) Signature r value
        :param sender_s: (``int``) Signature s value
        """
        super().__init__(
            chain_id,
            nonce,
            gas_price,
            gas_limit,
            destination,
            amount,
            data,
            access_list,
            y_parity,
            sender_r,
            sender_s,
        )

    @classmethod
    def from_rawtx(cls, rawtx: bytes) -> SignedType1Transaction:
        """Instantiate a SignedType1Transaction object from a raw encoded transaction.

        :param rawtx: (``bytes``) A raw signed transaction to instantiate with
        :returns: :class:`ledgereth.objects.SignedType1Transaction`
        """
        if rawtx[0] != cls.transaction_type:
            raise ValueError(
                f"Transaction is not a type {cls.transaction_type} transaction"
            )

        return SignedType1Transaction(
            *coerce_list_types(
                [int, int, int, int, bytes, int, bytes, None, int, int, int],
                decode(rawtx[1:]),
            )
        )

    def raw_transaction(self):
        """Return an encoded raw signed transaction.

        Encoded signed TX format spec:

        .. code::

            0x01 || rlp([chainId, nonce, gasPrice, gasLimit, destination, amount, data, accessList, signatureYParity, signatureR, signatureS])

        :returns: Encoded raw signed transaction bytes
        """  # noqa: E501
        return encode_hex(b"\x01" + bytes(encode(self, SignedType1Transaction)))

    # Match the API of the web3.py Transaction object
    #: Encoded raw signed transaction
    rawTransaction = property(raw_transaction)  # noqa: N815


class SignedType2Transaction(SerializableTransaction):
    """A signed Type 2 transaction."""

    fields = [  # noqa: RUF012
        ("chain_id", big_endian_int),
        ("nonce", big_endian_int),
        ("max_priority_fee_per_gas", big_endian_int),
        ("max_fee_per_gas", big_endian_int),
        ("gas_limit", big_endian_int),
        ("destination", address_allow_empty),
        ("amount", big_endian_int),
        ("data", binary),
        ("access_list", access_list_sede_type),
        ("y_parity", big_endian_int),
        ("sender_r", big_endian_int),
        ("sender_s", big_endian_int),
    ]

    #: The EIP-2718 transaction type
    transaction_type = TransactionType.EIP_1559

    def __init__(
        self,
        chain_id: int,
        nonce: int,
        max_priority_fee_per_gas: int,
        max_fee_per_gas: int,
        gas_limit: int,
        destination: bytes,
        amount: int,
        data: bytes,
        access_list: list[tuple[bytes, list[int]]],
        y_parity: int,
        sender_r: int,
        sender_s: int,
    ):
        """Initialize a signed type 2 transaction.

        :param chain_id: (``int``) Chain ID
        :param nonce: (``int``) Transaction nonce
        :param max_priority_fee_per_gas: (``int``) Priority fee per gas (in
            wei) to provide to the miner of the block.
        :param max_fee_per_gas: (``int``) Maximum fee in wei to pay for the
            transaction.  This is not compatible with :code:`gas_price`.
        :param gas_limit: (``int``) Gas limit
        :param destination: (``bytes``) Destination address
        :param amount: (``int``) Amount of Ether to send in wei
        :param data: (``bytes``) Transaction data
        :param access_list: (``list[tuple[bytes, list[int]]]``) EIP-2718 Access
            list
        :param y_parity: (``int``) Parity byte for the signature
        :param sender_r: (``int``) Signature r value
        :param sender_s: (``int``) Signature s value
        """
        super().__init__(
            chain_id,
            nonce,
            max_priority_fee_per_gas,
            max_fee_per_gas,
            gas_limit,
            destination,
            amount,
            data,
            access_list,
            y_parity,
            sender_r,
            sender_s,
        )

    @classmethod
    def from_rawtx(cls, rawtx: bytes) -> SignedType2Transaction:
        """Instantiate a SignedType2Transaction object from a raw encoded transaction.

        :param rawtx: (``bytes``) A raw signed transaction to instantiate with
        :returns: :class:`ledgereth.objects.SignedType2Transaction`
        """
        if rawtx[0] != cls.transaction_type:
            raise ValueError(
                f"Transaction is not a type {cls.transaction_type} transaction"
            )

        return SignedType2Transaction(
            *coerce_list_types(
                [
                    int,
                    int,
                    int,
                    int,
                    int,
                    bytes,
                    int,
                    bytes,
                    None,
                    int,
                    int,
                    int,
                ],
                decode(rawtx[1:]),
            )
        )

    def raw_transaction(self):
        """Return an encoded raw signed transaction.

        Encoded signed TX format spec:

        .. code::

            0x02 || rlp([chain_id, nonce, max_priority_fee_per_gas, max_fee_per_gas, gas_limit, destination, amount, data, access_list, signature_y_parity, signature_r, signature_s])

        :returns: Encoded raw signed transaction bytes
        """  # noqa: E501
        return encode_hex(b"\x02" + bytes(encode(self, SignedType2Transaction)))

    # Match the API of the web3.py Transaction object
    #: Encoded raw signed transaction
    rawTransaction = property(raw_transaction)  # noqa: N815


class Signed(ABC):
    """A signed object."""

    #: Signature v
    v: int
    #: Signature r
    r: int
    #: Signature s
    s: int

    def __init__(self, v, r, s):
        """Initialize a signed object."""
        self.v = v
        self.r = r
        self.s = s

    @property
    def signature(self):
        """Encoded signature.

        :returns: Signature ``bytes``
        """
        if not self.v or not self.r or not self.s:
            raise ValueError("Missing v, r, or s")

        return encode_hex(
            self.r.to_bytes(32, "big")
            + self.s.to_bytes(32, "big")
            + self.v.to_bytes(1, "big")
        )


class SignedMessage(Signed):
    """Signed EIP-191 message."""

    message: bytes

    def __init__(self, message, v, r, s):
        """Initialize a singed message.

        :param message: (``bytes``) Message that was signed
        :param v: (``int``) Signature v value
        :param r: (``int``) Signature r value
        :param s: (``int``) Signature s value
        """
        self.message = message
        super().__init__(v, r, s)


class SignedTypedMessage(Signed):
    """Signed EIP-812 typed data."""

    domain_hash: bytes
    message_hash: bytes

    def __init__(self, domain_hash, message_hash, v, r, s):
        """Initialize a singed message.

        :param domain_hash: (``bytes``) Domain hash that was signed
        :param message_hash: (``bytes``) Message hash that was signed
        :param v: (``int``) Signature v value
        :param r: (``int``) Signature r value
        :param s: (``int``) Signature s value
        """
        self.domain_hash = domain_hash
        self.message_hash = message_hash
        super().__init__(v, r, s)
