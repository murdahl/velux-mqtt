# Retrieves a window signing key from the VELUX gateway over its local "Netcom" protocol
# (TCP 25050, X25519 key exchange, ChaCha20-Poly1305). The gateway only opens the listener
# after a cloud "retrieve_key" request and a press of its physical button.
# Adapted from Niek/ha-velux-active (MIT).
import base64
import hashlib
import socket
import struct
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from .signing import SigningKey

NETCOM_PORT = 25050
FRAME_PING = 0x0000
FRAME_PONG = 0x0001
FRAME_CLOSE = 0x0008
FRAME_SECURE = 0x0200
FRAME_ECDH_REQUEST = 0x0201
FRAME_ECDH_RESPONSE = 0x0202
FRAME_NONCE_REQUEST = 0x020A
FRAME_NONCE_RESPONSE = 0x020B
FRAME_CHALLENGE_REQUEST = 0x020C
FRAME_CHALLENGE_RESPONSE = 0x020D
FRAME_KEY_REQUEST = 0x020E
FRAME_KEY_RESPONSE = 0x020F


class PairingError(Exception):
    pass


def _frame(frame_type: int, payload: bytes = b"") -> bytes:
    return struct.pack("<HH", frame_type, len(payload)) + payload


def _next_nonce(nonce: bytearray) -> None:
    for index in range(11, 3, -1):
        if nonce[index] < 0xFF:
            nonce[index] += 1
            return
        nonce[index] = 0
    nonce[:] = bytes(12)


class _Connection:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.buffer = bytearray()
        self.aead: ChaCha20Poly1305 | None = None
        self.tx_nonce = bytearray(12)
        self.rx_nonce = bytearray(12)

    def close(self) -> None:
        self.socket.close()

    def send(self, frame: bytes) -> None:
        if self.aead:
            encrypted = self.aead.encrypt(bytes(self.tx_nonce), frame, None)
            _next_nonce(self.tx_nonce)
            frame = _frame(FRAME_SECURE, encrypted)
        self.socket.sendall(frame)

    def _read(self, count: int) -> None:
        while len(self.buffer) < count:
            chunk = self.socket.recv(4096)
            if not chunk:
                raise PairingError("The gateway closed the connection")
            self.buffer.extend(chunk)

    def receive(self, expected: int) -> bytes:
        while True:
            self._read(4)
            frame_type, size = struct.unpack_from("<HH", self.buffer)
            self._read(4 + size)
            payload = bytes(self.buffer[4 : 4 + size])
            del self.buffer[: 4 + size]

            if frame_type == FRAME_PING:
                self.send(_frame(FRAME_PONG))
                continue
            if self.aead and frame_type == FRAME_SECURE:
                decrypted = self.aead.decrypt(bytes(self.rx_nonce), payload, None)
                _next_nonce(self.rx_nonce)
                frame_type, size = struct.unpack_from("<HH", decrypted)
                payload = decrypted[4:]
                if len(payload) != size:
                    raise PairingError("Malformed encrypted frame")
            if frame_type != expected:
                raise PairingError(f"Expected frame 0x{expected:04x}, got 0x{frame_type:04x}")
            return payload


def _handshake(connection: _Connection) -> None:
    connection.send(_frame(FRAME_PING))
    connection.receive(FRAME_PONG)

    private_key = x25519.X25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    connection.send(_frame(FRAME_ECDH_REQUEST, b"\x01" + public_key))
    gateway_key = connection.receive(FRAME_ECDH_RESPONSE)
    if len(gateway_key) == 33 and gateway_key[0] in (0, 1):
        gateway_key = gateway_key[1:]
    if len(gateway_key) != 32:
        raise PairingError("Unexpected gateway public key")

    secret = private_key.exchange(x25519.X25519PublicKey.from_public_bytes(gateway_key))
    connection.aead = ChaCha20Poly1305(hashlib.sha512(secret).digest()[:32])


def _request_key(connection: _Connection, key_id: bytes) -> bytes:
    connection.send(_frame(FRAME_KEY_REQUEST, b"\x00" + key_id))
    response = connection.receive(FRAME_KEY_RESPONSE)
    if not response or response[0] != 0:
        reason = {1: "rejected", 2: "bad key id", 3: "key table full"}.get(response[0] if response else -1, "unknown")
        raise PairingError(f"The gateway refused the key request ({reason})")
    if len(response) != 33:
        raise PairingError("Unexpected key length")
    return response[1:]


def _verify_key(connection: _Connection, key_id: bytes, key: bytes) -> None:
    connection.send(_frame(FRAME_NONCE_REQUEST, b"\x00"))
    nonce = connection.receive(FRAME_NONCE_RESPONSE)
    challenge = hashlib.sha512(key_id + key + nonce).digest()
    connection.send(_frame(FRAME_CHALLENGE_REQUEST, key_id + challenge))
    response = connection.receive(FRAME_CHALLENGE_RESPONSE)
    if not response or response[0] != 0:
        raise PairingError("The gateway rejected the key challenge")


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def retrieve_signing_key(host: str, gateway_id: str | None, wait_seconds: int = 120, port: int = NETCOM_PORT) -> SigningKey:
    """Waits for the gateway's pairing listener (after the button press) and retrieves a key."""
    deadline = time.monotonic() + wait_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if _port_open(host, port):
            key_id = uuid.uuid4().bytes
            connection = _Connection(host, port, timeout=10)
            try:
                _handshake(connection)
                key = _request_key(connection, key_id)
                _verify_key(connection, key_id, key)
                connection.send(_frame(FRAME_CLOSE))
                return SigningKey(
                    base64.urlsafe_b64encode(key_id).decode(),
                    base64.urlsafe_b64encode(key).decode(),
                    gateway_id,
                )
            except (OSError, PairingError, ValueError) as error:
                last_error = error
            finally:
                connection.close()
        time.sleep(1)
    raise PairingError(f"No key retrieved from {host}:{port}: {last_error or 'the pairing listener never opened'}")
