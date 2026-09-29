import logging
import os
import signal
import sys

from .bridge import run
from .config import Config, ConfigError
from .pair import pair


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request at INFO, which would log the API calls every poll
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Docker stops containers with SIGTERM; exit through run()'s cleanup to publish "offline"
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    pairing = sys.argv[1:2] == ["pair"]
    if pairing and len(sys.argv) != 3:
        sys.exit("Usage: velux-mqtt pair <gateway ip or hostname>")

    try:
        # Pairing talks to the cloud and the gateway only, so it doesn't need an MQTT broker
        config = Config.from_env({**os.environ, "MQTT_HOST": os.environ.get("MQTT_HOST") or "unused"} if pairing else os.environ)
    except ConfigError as error:
        sys.exit(str(error))

    if pairing:
        sys.exit(pair(config, sys.argv[2]))

    try:
        run(config)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
