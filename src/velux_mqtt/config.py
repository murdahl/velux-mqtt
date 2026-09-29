import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# Public app credentials embedded in the VELUX ACTIVE app (see IngmarStein/ha-velux-active)
DEFAULT_CLIENT_ID = "5931426da127d981e76bdd3f"
DEFAULT_CLIENT_SECRET = "6ae2d89d15e767ae5c56b456b452d319"
MIN_POLL_INTERVAL = 10


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    velux_user: str
    velux_password: str
    mqtt_host: str
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic_prefix: str = "velux"
    poll_interval: float = 60
    enable_control: bool = False
    token_file: Path = Path("/data/token.json")
    client_id: str = DEFAULT_CLIENT_ID
    client_secret: str = DEFAULT_CLIENT_SECRET

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        missing = [name for name in ("VELUX_USER", "VELUX_PASSWORD", "MQTT_HOST") if not env.get(name)]
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

        try:
            poll_interval = float(env.get("POLL_INTERVAL", cls.poll_interval))
            mqtt_port = int(env.get("MQTT_PORT", cls.mqtt_port))
        except ValueError as error:
            raise ConfigError(f"Invalid numeric setting: {error}") from error
        if poll_interval < MIN_POLL_INTERVAL:
            raise ConfigError(f"POLL_INTERVAL must be at least {MIN_POLL_INTERVAL} seconds (cloud API rate limits)")

        return cls(
            velux_user=env["VELUX_USER"],
            velux_password=env["VELUX_PASSWORD"],
            mqtt_host=env["MQTT_HOST"],
            mqtt_port=mqtt_port,
            mqtt_username=env.get("MQTT_USERNAME") or None,
            mqtt_password=env.get("MQTT_PASSWORD") or None,
            mqtt_topic_prefix=env.get("MQTT_TOPIC_PREFIX", cls.mqtt_topic_prefix).rstrip("/"),
            poll_interval=poll_interval,
            enable_control=env.get("ENABLE_CONTROL", "").strip().lower() in ("1", "true", "yes"),
            token_file=Path(env.get("TOKEN_FILE", str(cls.token_file))),
            client_id=env.get("VELUX_CLIENT_ID") or DEFAULT_CLIENT_ID,
            client_secret=env.get("VELUX_CLIENT_SECRET") or DEFAULT_CLIENT_SECRET,
        )
