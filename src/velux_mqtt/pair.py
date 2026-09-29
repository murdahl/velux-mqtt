import httpx

from .api import TokenStore, VeluxClient, VeluxError
from .config import Config
from .model import GATEWAY_TYPE, pick_home_id
from .pairing import PairingError, retrieve_signing_key
from .signing import SigningKeyStore


def pair(config: Config, gateway_host: str) -> int:
    """One-time: retrieve the key that roof-window commands must be signed with."""
    with httpx.Client(timeout=20, headers={"User-Agent": "velux-mqtt"}) as http:
        client = VeluxClient(
            http, config.velux_user, config.velux_password, config.client_id, config.client_secret, TokenStore(config.token_file)
        )
        try:
            homes = client.homes_data()
            home_id = pick_home_id(homes)
            home = next(home for home in homes["body"]["homes"] if home["id"] == home_id)
            gateway_id = next(module["id"] for module in home["modules"] if module.get("type") == GATEWAY_TYPE)
            client.request_key_retrieval(home_id, gateway_id)
        except (httpx.HTTPError, VeluxError, LookupError, StopIteration) as error:
            print(f"Couldn't put the gateway into pairing mode: {error}")
            return 1

    print("The gateway is in pairing mode. When its light starts flashing, press the button on the gateway.")
    print(f"Waiting up to 2 minutes for {gateway_host}:25050 ...")
    try:
        key = retrieve_signing_key(gateway_host, gateway_id)
    except PairingError as error:
        print(f"Pairing failed: {error}")
        return 1

    SigningKeyStore(config.signing_key_file).save(key)
    print(f"Paired. Signing key saved to {config.signing_key_file}; restart velux-mqtt to control the windows.")
    return 0
