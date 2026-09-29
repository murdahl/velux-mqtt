# Signed roof-window commands. VELUX only accepts window movements signed with a key
# retrieved from the gateway (see pairing.py). Adapted from Niek/ha-velux-active (MIT).
import base64
import hashlib
import hmac
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SigningKey:
    sign_key_id: str
    hash_sign_key: str  # URL-safe base64
    gateway_id: str | None = None


class SigningKeyStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SigningKey | None:
        try:
            return SigningKey(**json.loads(self.path.read_text()))
        except (OSError, ValueError, TypeError):
            return None

    def save(self, key: SigningKey) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(key)))
        os.chmod(self.path, 0o600)


def _decode_key(hash_sign_key: str) -> bytes:
    value = hash_sign_key.strip().replace("-", "+").replace("_", "/")
    value += "=" * (-len(value) % 4)
    return base64.b64decode(value, validate=True)


def position_hash(hash_sign_key: str, position: int, timestamp: int, nonce: int, module_id: str) -> str:
    message = f"target_position{position}{timestamp}{nonce}{module_id}"
    digest = hmac.new(_decode_key(hash_sign_key), message.encode(), hashlib.sha512).digest()
    return base64.b64encode(digest).decode().replace("+", "-").replace("/", "_")


class Signer:
    """Signs batches of window commands, never reusing a (timestamp, nonce) pair."""

    def __init__(self, key: SigningKey) -> None:
        self.key = key
        self._last_timestamp = 0
        self._last_nonce = -1

    def _allocate(self, now: int, count: int) -> tuple[int, int]:
        if now <= self._last_timestamp:
            timestamp, base = self._last_timestamp, self._last_nonce + 1
        else:
            timestamp, base = now, 0
        self._last_timestamp, self._last_nonce = timestamp, base + count - 1
        return timestamp, base

    def sign(self, bridge_id: str, targets: list[tuple[str, int]], now: int) -> list[dict]:
        """targets: (module id, position). Returns setstate module entries."""
        timestamp, base = self._allocate(now, len(targets))
        return [
            {
                "id": module_id,
                "bridge": bridge_id,
                "nonce": base + offset,
                "timestamp": timestamp,
                "sign_key_id": self.key.sign_key_id,
                "target_position": position,
                # Rain override: the reported rain state can be stale; VELUX still limits openings while raining
                "force": True,
                "hash_target_position": position_hash(self.key.hash_sign_key, position, timestamp, base + offset, module_id),
            }
            for offset, (module_id, position) in enumerate(targets)
        ]
