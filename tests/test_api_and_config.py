import json
import time

import httpx
import pytest
from fixtures import HOME, HOMES_DATA

from velux_mqtt.api import AuthError, Tokens, TokenStore, VeluxClient
from velux_mqtt.config import Config, ConfigError

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


def test_set_positions_payload(tmp_path):
    api = FakeApi()
    client = client_with(api, tmp_path, Tokens("access-1", "refresh-0", time.time() + 3600))

    client.set_positions(HOME, [("gw", "w1", 100), ("gw", "w2", 100)])

    path, body = api.requests[-1]
    assert path == "/syncapi/v1/setstate"
    assert body == {"home": {"id": HOME, "modules": [
        {"bridge": "gw", "id": "w1", "target_position": 100},
        {"bridge": "gw", "id": "w2", "target_position": 100},
    ]}}


def test_config():
    config = Config.from_env(ENV | {"ENABLE_CONTROL": "true", "POLL_INTERVAL": "30"})

    assert config.enable_control is True
    assert config.poll_interval == 30
    assert config.mqtt_topic_prefix == "velux"

    with pytest.raises(ConfigError, match="VELUX_USER, VELUX_PASSWORD"):
        Config.from_env({"MQTT_HOST": "x"})
    with pytest.raises(ConfigError, match="at least 10"):
        Config.from_env(ENV | {"POLL_INTERVAL": "2"})
