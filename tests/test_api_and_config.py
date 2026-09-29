import base64
import json
import time

import httpx
import pytest
from fixtures import GATEWAY, HOME, HOME_STATUS, HOMES_DATA

from velux_mqtt.api import AuthError, SigningRequired, Tokens, TokenStore, VeluxClient, VeluxError
from velux_mqtt.config import Config, ConfigError
from velux_mqtt.model import build_house
from velux_mqtt.signing import Signer, SigningKey, position_hash

ENV = {"VELUX_USER": "me@example.com", "VELUX_PASSWORD": "secret", "MQTT_HOST": "mosquitto"}


class FakeApi:
    """Records requests and answers like the VELUX cloud."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.valid_tokens = {"access-1"}
        self.issued = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = dict(httpx.QueryParams(request.content.decode())) if request.headers.get("content-type", "").startswith("application/x-www-form") else json.loads(request.content or b"{}")
        self.requests.append((request.url.path, body))

        if request.url.path == "/oauth2/token":
            if body["grant_type"] == "refresh_token" and body["refresh_token"] == "expired":
                return httpx.Response(400)
            self.issued += 1
            token = f"access-{self.issued + 1}"
            self.valid_tokens.add(token)
            return httpx.Response(200, json={"access_token": token, "refresh_token": f"refresh-{self.issued}", "expires_in": 10800})

        token = body.get("access_token") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.valid_tokens:
            return httpx.Response(403)
        if request.url.path == "/api/homesdata":
            return httpx.Response(200, json=HOMES_DATA)
        return httpx.Response(200, json={"status": "ok"})

    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]


def client_with(api: FakeApi, tmp_path, tokens: Tokens | None = None) -> VeluxClient:
    store = TokenStore(tmp_path / "token.json")
    if tokens:
        store.save(tokens)
    http = httpx.Client(transport=httpx.MockTransport(api.handler))
    return VeluxClient(http, "me@example.com", "secret", "cid", "csecret", store)


def test_reuses_saved_token_without_logging_in(tmp_path):
    api = FakeApi()
    client = client_with(api, tmp_path, Tokens("access-1", "refresh-0", time.time() + 3600))

    client.homes_data()

    assert api.paths() == ["/api/homesdata"]


def test_logs_in_and_saves_token(tmp_path):
    api = FakeApi()
    client = client_with(api, tmp_path)

    client.homes_data()

    assert api.paths() == ["/oauth2/token", "/api/homesdata"]
    assert api.requests[0][1]["grant_type"] == "password"
    assert TokenStore(tmp_path / "token.json").load().access_token == "access-2"
    assert (tmp_path / "token.json").stat().st_mode & 0o777 == 0o600


def test_expired_refresh_token_falls_back_to_password(tmp_path):
    api = FakeApi()
    client = client_with(api, tmp_path, Tokens("old", "expired", time.time() - 10))

    client.homes_data()

    assert [body.get("grant_type") for path, body in api.requests if path == "/oauth2/token"] == ["refresh_token", "password"]


def test_revoked_token_retries_once(tmp_path):
    api = FakeApi()
    client = client_with(api, tmp_path, Tokens("revoked", "refresh-0", time.time() + 3600))

    client.homes_data()

    assert api.paths() == ["/api/homesdata", "/oauth2/token", "/api/homesdata"]


def test_rejected_login_raises(tmp_path):
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    client = VeluxClient(http, "me", "wrong", "cid", "csecret", TokenStore(tmp_path / "token.json"))

    with pytest.raises(AuthError):
        client.homes_data()


def test_blinds_are_sent_unsigned(tmp_path):
    api = FakeApi()
    client = client_with(api, tmp_path, Tokens("access-1", "refresh-0", time.time() + 3600))
    house = build_house(HOMES_DATA, HOME_STATUS, HOME)
    blind = house.covers[2]

    client.move(house, [(blind, 100)], signer=None)

    path, body = api.requests[-1]
    assert path == "/syncapi/v1/setstate"
    assert body == {"home": {"id": HOME, "modules": [{"bridge": GATEWAY, "id": "79000001ffffffff", "target_position": 100}]}}


def test_windows_need_a_signing_key(tmp_path):
    api = FakeApi()
    client = client_with(api, tmp_path, Tokens("access-1", "refresh-0", time.time() + 3600))
    house = build_house(HOMES_DATA, HOME_STATUS, HOME)

    with pytest.raises(SigningRequired):
        client.move(house, [(house.covers[0], 100)], signer=None)
    assert api.requests == []


def test_windows_are_signed(tmp_path):
    api = FakeApi()
    client = client_with(api, tmp_path, Tokens("access-1", "refresh-0", time.time() + 3600))
    house = build_house(HOMES_DATA, HOME_STATUS, HOME)
    signer = Signer(SigningKey("key-id", base64.urlsafe_b64encode(b"k" * 32).decode()))

    client.move(house, [(house.covers[0], 7), (house.covers[1], 7)], signer)

    body = api.requests[-1][1]
    assert body["app_type"] == "app_velux"
    assert body["home"]["timezone"] == "Europe/Oslo"
    first, second = body["home"]["modules"]
    assert (first["id"], first["target_position"], first["nonce"], first["force"]) == ("5300000000000001", 7, 0, True)
    assert second["nonce"] == 1 and second["timestamp"] == first["timestamp"]
    assert first["hash_target_position"] == position_hash(signer.key.hash_sign_key, 7, first["timestamp"], 0, "5300000000000001")


def test_api_level_errors_raise(tmp_path):
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"body": {"errors": [{"code": 2}]}})))
    client = VeluxClient(http, "me", "pw", "cid", "cs", TokenStore(tmp_path / "t.json"))
    client.tokens = Tokens("a", "r", time.time() + 3600)

    with pytest.raises(VeluxError, match="rejected"):
        client.stop_all(HOME, GATEWAY)


def test_config():
    config = Config.from_env(ENV | {"ENABLE_CONTROL": "true", "POLL_INTERVAL": "30"})

    assert config.enable_control is True
    assert config.poll_interval == 30
    assert config.mqtt_topic_prefix == "velux"

    with pytest.raises(ConfigError, match="VELUX_USER, VELUX_PASSWORD"):
        Config.from_env({"MQTT_HOST": "x"})
    with pytest.raises(ConfigError, match="at least 10"):
        Config.from_env(ENV | {"POLL_INTERVAL": "2"})
