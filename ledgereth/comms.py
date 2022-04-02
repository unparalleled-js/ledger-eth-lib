from typing import Any

from eth_utils import add_0x_prefix
from ledgerblue.comm import getDongle
from ledgerblue.commException import CommException

from ledgereth.constants import DEFAULT_PATH_ENCODED
from ledgereth.exceptions import LedgerError
from ledgereth.objects import ISO7816Command

DONGLE_CACHE = None
DONGLE_CONFIG_CACHE = None


class LedgerCommands:
    """APDU commands for communication with ledger-app-eth.  Tested only on a Ledger Nano S.

    See `ledger-app-eth<https://github.com/LedgerHQ/ledger-app-eth/blob/master/doc/ethapp.asc>`_
    documentation.
    """

    GET_CONFIGURATION = ISO7816Command(
        CLA=b"\xe0",
        INS=b"\x06",
        P1=b"\x00",
        P2=b"\x00",
        Lc=b"\x00",
        Le=b"\x04",
    )

    GET_DEFAULT_ADDRESS_NO_CONFIRM = ISO7816Command(
        CLA=b"\xe0",
        INS=b"\x02",
        P1=b"\x00",  # 0x00 - Return addres | 0x01 - Confirm befor ereturning
        P2=b"\x00",  # 0x00 - No chain code | 0x01 - With chain code
        Lc=len(DEFAULT_PATH_ENCODED).to_bytes(1, "big"),  # Payload byte length
        data=(len(DEFAULT_PATH_ENCODED) // 4).to_bytes(1, "big") + DEFAULT_PATH_ENCODED,
    )

    GET_ADDRESS_NO_CONFIRM = ISO7816Command(
        CLA=b"\xe0",
        INS=b"\x02",
        P1=b"\x00",  # 0x00 - Return addres | 0x01 - Confirm befor ereturning
        P2=b"\x00",  # 0x00 - No chain code | 0x01 - With chain code
        # Lc=len(DEFAULT_PATH_ENCODED).to_bytes(1, 'big'),  # Payload byte length
        # data=(len(DEFAULT_PATH_ENCODED) // 4).to_bytes(1, 'big') + DEFAULT_PATH_ENCODED,
    )

    SIGN_TX_FIRST_DATA = ISO7816Command(
        CLA=b"\xe0",
        INS=b"\x04",
        P1=b"\x00",  # 0x00 - First TX data block | 0x80 - Secondary data block
        P2=b"\x00",
    )

    SIGN_TX_SECONDARY_DATA = ISO7816Command(
        CLA=b"\xe0",
        INS=b"\x04",
        P1=b"\x80",  # 0x00 - First TX data block | 0x80 - Secondary data block
        P2=b"\x00",
    )

    SIGN_MESSAGE_FIRST_DATA = ISO7816Command(
        CLA=b"\xe0",
        INS=b"\x08",
        P1=b"\x00",  # 0x00 - First TX data block | 0x80 - Secondary data block
        P2=b"\x00",
    )

    SIGN_MESSAGE_SECONDARY_DATA = ISO7816Command(
        CLA=b"\xe0",
        INS=b"\x08",
        P1=b"\x80",  # 0x00 - First TX data block | 0x80 - Secondary data block
        P2=b"\x00",
    )

    SIGN_TYPED_FIRST_DATA = ISO7816Command(
        CLA=b"\xe0",
        INS=b"\x0c",
        P1=b"\x00",
        P2=b"\x00",
    )

    @staticmethod
    def get(name: str) -> bytes:
        if not hasattr(LedgerCommands, name):
            raise ValueError("Command not available")
        cmd = getattr(LedgerCommands, name)
        return cmd.encode()

    @staticmethod
    def get_with_data(
        name: str, data: bytes, Lc: bytes = None, Le: bytes = None
    ) -> bytes:
        if not hasattr(LedgerCommands, name):
            raise ValueError("Command not available")
        cmd = getattr(LedgerCommands, name)
        cmd.set_data(data)
        if Lc is not None:
            cmd.Lc = Lc
        if Le is not None:
            cmd.Le = Le
        return cmd.encode()


def dongle_send(dongle, command_string: str) -> bytes:
    """Send a command to the dongle"""
    hex_command = LedgerCommands.get(command_string)
    try:
        return dongle.exchange(hex_command)
    except CommException as err:
        raise LedgerError.transalate_comm_exception(err) from err


def dongle_send_data(
    dongle, command_string: str, data: bytes, Lc: bytes = None, Le: bytes = None
) -> bytes:
    """Send a command with data to the dongle"""
    hex_command = LedgerCommands.get_with_data(command_string, data, Lc=Lc, Le=Le)
    try:
        return dongle.exchange(hex_command)
    except CommException as err:
        raise LedgerError.transalate_comm_exception(err) from err


def decode_response_version_from_config(confbytes: bytes) -> str:
    """Decode the string version from the bytearray response from Ledger device"""
    return "{}.{}.{}".format(
        confbytes[1],
        confbytes[2],
        confbytes[3],
    )


def decode_response_address(response):
    """Decode an address response from the dongle"""
    offset = 1 + response[0]
    address_encoded = response[offset + 1 : offset + 1 + response[offset]]
    return add_0x_prefix(address_encoded.decode("ascii"))


def is_usable_version(confbytes: bytes) -> bool:
    """Only tested on 1.2.4"""
    version = decode_response_version_from_config(confbytes)
    v_parts = version.split(".")
    ver = [int(s) for s in v_parts]
    # v9.9.9 is MockLedger
    return ver[0] == 9 or not any([ver[0] != 1, ver[1] < 2, ver[2] < 4])


def init_dongle(dongle: Any = None, debug: bool = False):
    """Initialize the dongle and sanity check the connection"""
    global DONGLE_CACHE, DONGLE_CONFIG_CACHE
    dong = dongle

    # If not given, use cache if available
    if dong is None:
        if DONGLE_CACHE is None:
            try:
                DONGLE_CACHE = getDongle(debug)
            except CommException as err:
                raise LedgerError.transalate_comm_exception(err) from err

        dong = DONGLE_CACHE

    # Sanity check the version
    if DONGLE_CONFIG_CACHE is None or dongle is not None:
        DONGLE_CONFIG_CACHE = dongle_send(dong, "GET_CONFIGURATION")

    if not is_usable_version(DONGLE_CONFIG_CACHE):
        raise NotImplementedError("Unsupported firmware version")

    return dong
