import base64

import pytest
from fake_gateway import FakeGateway

from velux_mqtt.pairing import PairingError, retrieve_signing_key
from velux_mqtt.signing import Signer, SigningKey, SigningKeyStore, position_hash


@pytest.fixture
def gateway():
    gateway = FakeGateway()
    yield gateway
    gateway.close()


def test_retrieves_key_from_gateway(gateway):
    key = retrieve_signing_key("127.0.0.1", "70:ee:50:00:00:01", wait_seconds=5, port=gateway.port)

    assert gateway.error is None
    assert base64.urlsafe_b64decode(key.hash_sign_key) == gateway.key
    assert base64.urlsafe_b64decode(key.sign_key_id) == gateway.key_id
    assert key.gateway_id == "70:ee:50:00:00:01"


def test_rejected_challenge_fails():
    gateway = FakeGateway(accept_challenge=False)
    try:
        with pytest.raises(PairingError, match="rejected the key challenge"):
            retrieve_signing_key("127.0.0.1", None, wait_seconds=2, port=gateway.port)
    finally:
        gateway.close()


def test_no_listener_times_out():
    with pytest.raises(PairingError, match="never opened"):
        retrieve_signing_key("127.0.0.1", None, wait_seconds=1, port=1)


def test_key_store_roundtrip(tmp_path):
    store = SigningKeyStore(tmp_path / "key.json")
    key = SigningKey("id", "c2VjcmV0", "gw")
    store.save(key)

    assert store.load() == key
    assert (tmp_path / "key.json").stat().st_mode & 0o777 == 0o600


def test_nonces_never_repeat_within_a_second():
    signer = Signer(SigningKey("id", base64.urlsafe_b64encode(b"k" * 32).decode()))

    first = signer.sign("gw", [("w1", 0), ("w2", 0)], now=1000)
    second = signer.sign("gw", [("w1", 100)], now=1000)
    later = signer.sign("gw", [("w1", 50)], now=1001)

    assert [module["nonce"] for module in first + second] == [0, 1, 2]
    assert {module["timestamp"] for module in first + second} == {1000}
    assert (later[0]["timestamp"], later[0]["nonce"]) == (1001, 0)


def test_hash_matches_known_vector():
    key = base64.b64encode(bytes(range(32))).decode()

    # Computed independently with Niek/ha-velux-active's signing.compute_hash
    assert position_hash(key, 7, 1790686155, 0, "5336272614300225") == KNOWN_HASH


KNOWN_HASH = "z5ptuD_UUSRhGDWf5Ea2eLBtAyBOKS16iG5-Sd77c8WPtJNtSrJDYiE56mdmiZ4o6r-BHPIZkeuCt0kxGJC7nQ=="
