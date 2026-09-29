"""A stand-in for the VELUX gateway's local pairing listener, speaking the gateway side of the protocol."""
import hashlib
import os
import socket
import struct
import threading

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from velux_mqtt import pairing as p


class FakeGateway:
    def __init__(self, accept_challenge: bool = True) -> None:
        self.accept_challenge = accept_challenge
        self.key = os.urandom(32)
        self.key_id: bytes | None = None
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.error: Exception | None = None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.close()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            data = connection.recv(1, socket.MSG_PEEK)
            if not data:
                connection.close()  # a port probe
                continue
            try:
                self._session(connection)
            except Exception as error:  # surfaced through self.error in the tests
                self.error = error
            finally:
                connection.close()

    def _session(self, connection: socket.socket) -> None:
        buffer = bytearray()
        aead: ChaCha20Poly1305 | None = None
        tx, rx = bytearray(12), bytearray(12)

        def read() -> tuple[int, bytes]:
            nonlocal buffer
            while len(buffer) < 4 or len(buffer) < 4 + struct.unpack_from("<HH", buffer)[1]:
                chunk = connection.recv(4096)
                if not chunk:
                    raise ConnectionError("client went away")
                buffer.extend(chunk)
            frame_type, size = struct.unpack_from("<HH", buffer)
            payload = bytes(buffer[4 : 4 + size])
            del buffer[: 4 + size]
            if aead is not None:
                assert frame_type == p.FRAME_SECURE
                inner = aead.decrypt(bytes(rx), payload, None)
                p._next_nonce(rx)
                frame_type, size = struct.unpack_from("<HH", inner)
                payload = inner[4:]
            return frame_type, payload

        def send(frame_type: int, payload: bytes = b"") -> None:
            frame = p._frame(frame_type, payload)
            if aead is not None:
                frame = p._frame(p.FRAME_SECURE, aead.encrypt(bytes(tx), frame, None))
                p._next_nonce(tx)
            connection.sendall(frame)

        assert read()[0] == p.FRAME_PING
        send(p.FRAME_PONG)

        frame_type, payload = read()
        assert frame_type == p.FRAME_ECDH_REQUEST and payload[0] == 1
        private_key = x25519.X25519PrivateKey.generate()
        public = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        send(p.FRAME_ECDH_RESPONSE, b"\x01" + public)
        secret = private_key.exchange(x25519.X25519PublicKey.from_public_bytes(payload[1:]))
        aead = ChaCha20Poly1305(hashlib.sha512(secret).digest()[:32])

        frame_type, payload = read()
        assert frame_type == p.FRAME_KEY_REQUEST and payload[0] == 0
        self.key_id = payload[1:]
        send(p.FRAME_KEY_RESPONSE, b"\x00" + self.key)

        assert read()[0] == p.FRAME_NONCE_REQUEST
        nonce = os.urandom(16)
        send(p.FRAME_NONCE_RESPONSE, nonce)

        frame_type, payload = read()
        assert frame_type == p.FRAME_CHALLENGE_REQUEST
        expected = self.key_id + hashlib.sha512(self.key_id + self.key + nonce).digest()
        send(p.FRAME_CHALLENGE_RESPONSE, b"\x00" if self.accept_challenge and payload == expected else b"\x01")

        assert read()[0] == p.FRAME_CLOSE
