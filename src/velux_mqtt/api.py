import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from .model import Cover, House
from .signing import Signer

log = logging.getLogger(__name__)

BASE_URL = "https://app.velux-active.com"
# Signed window commands identify as the VELUX app
APP_TYPE = "app_velux"
APP_VERSION = "791302006"
# Refresh a little before the token actually expires
EXPIRY_MARGIN = 60


class VeluxError(Exception):
    pass


class AuthError(VeluxError):
    pass


class SigningRequired(VeluxError):
    pass


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float

    @property
    def valid(self) -> bool:
        return time.time() < self.expires_at - EXPIRY_MARGIN


class TokenStore:
    """Keeps tokens across restarts, so the bridge doesn't log in (and trigger a login email) every start."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Tokens | None:
        try:
            return Tokens(**json.loads(self.path.read_text()))
        except (OSError, ValueError, TypeError):
            return None

    def save(self, tokens: Tokens) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(asdict(tokens)))
            os.chmod(self.path, 0o600)
        except OSError as error:
            log.warning("Couldn't save tokens to %s: %s", self.path, error)


class VeluxClient:
    """Client for the VELUX ACTIVE cloud API, the same API the official app uses."""

    def __init__(
        self,
        http: httpx.Client,
        username: str,
        password: str,
        client_id: str,
        client_secret: str,
        token_store: TokenStore,
    ) -> None:
        self.http = http
        self.username = username
        self.password = password
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_store = token_store
        self.tokens = token_store.load()

    def _token_request(self, data: dict[str, str]) -> Tokens:
        response = self.http.post(
            f"{BASE_URL}/oauth2/token",
            data={"client_id": self.client_id, "client_secret": self.client_secret, **data},
        )
        if response.status_code in (400, 401, 403):
            raise AuthError(f"Token request rejected ({response.status_code})")
        response.raise_for_status()
        body = response.json()
        tokens = Tokens(body["access_token"], body["refresh_token"], time.time() + body.get("expires_in", 10800))
        self.tokens = tokens
        self.token_store.save(tokens)
        return tokens

    def login(self) -> None:
        log.info("Logging in to VELUX ACTIVE as %s", self.username)
        self._token_request(
            {"grant_type": "password", "username": self.username, "password": self.password, "user_prefix": "velux"}
        )

    def _refresh(self) -> None:
        if not self.tokens:
            self.login()
            return
        try:
            self._token_request({"grant_type": "refresh_token", "refresh_token": self.tokens.refresh_token})
        except AuthError:
            self.login()

    def _access_token(self) -> str:
        if not self.tokens or not self.tokens.valid:
            self._refresh()
        return self.tokens.access_token

    def _post(self, path: str, **kwargs) -> dict:
        for attempt in range(2):
            token = self._access_token()
            if "data" in kwargs:
                response = self.http.post(f"{BASE_URL}{path}", data={**kwargs["data"], "access_token": token})
            else:
                response = self.http.post(f"{BASE_URL}{path}", json=kwargs["json"], headers={"Authorization": f"Bearer {token}"})
            if response.status_code in (401, 403) and attempt == 0:
                # Token revoked (e.g. password changed); get a new one and retry once
                self.tokens = None
                continue
            if response.status_code in (401, 403):
                raise AuthError(f"{path} rejected ({response.status_code})")
            response.raise_for_status()
            return response.json()
        raise AssertionError("unreachable")

    def homes_data(self) -> dict:
        return self._post("/api/homesdata", data={})

    def home_status(self, home_id: str) -> dict:
        return self._post("/syncapi/v1/homestatus", json={"home_id": home_id})

    def _setstate(self, body: dict) -> None:
        response = self._post("/syncapi/v1/setstate", json=body)
        # The API can answer 200 with per-module errors
        errors = (response.get("body") or {}).get("errors") if isinstance(response, dict) else None
        if errors:
            raise VeluxError(f"VELUX rejected the command: {errors}")

    def move(self, house: House, targets: list[tuple[Cover, int]], signer: Signer | None) -> None:
        """Blinds take plain commands; roof windows need commands signed with the gateway key."""
        plain = [(cover, position) for cover, position in targets if not cover.is_window]
        windows = [(cover, position) for cover, position in targets if cover.is_window]

        if plain:
            modules = [{"bridge": cover.bridge, "id": cover.id, "target_position": position} for cover, position in plain]
            self._setstate({"home": {"id": house.home_id, "modules": modules}})
        if windows:
            if signer is None:
                raise SigningRequired("Roof windows need a signing key: run `velux-mqtt pair <gateway ip>` once")
            modules = signer.sign(windows[0][0].bridge, [(cover.id, position) for cover, position in windows], int(time.time()))
            self._setstate({
                "app_type": APP_TYPE,
                "app_version": APP_VERSION,
                "home": {"id": house.home_id, "timezone": house.timezone, "modules": modules},
            })

    def stop_all(self, home_id: str, bridge_id: str) -> None:
        self._setstate({"home": {"id": home_id, "modules": [{"id": bridge_id, "stop_movements": "all"}]}})

    def request_key_retrieval(self, home_id: str, bridge_id: str) -> None:
        """Asks the gateway to open its local pairing listener; it then waits for its button to be pressed."""
        self._setstate({"home": {"id": home_id, "modules": [{"id": bridge_id, "retrieve_key": True}]}})
