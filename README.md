# velux-mqtt

Publishes VELUX ACTIVE (KIX 300) roof windows, blinds and shutters to MQTT, and optionally controls them. It uses the VELUX ACTIVE cloud API, the same API the official app uses, so the app keeps working and no HomeKit pairing is needed.

## Run

```sh
docker run -d --name velux-mqtt --restart unless-stopped \
  -e VELUX_USER=you@example.com -e VELUX_PASSWORD=secret \
  -e MQTT_HOST=192.168.1.10 -e MQTT_USERNAME=velux -e MQTT_PASSWORD=secret \
  -v velux-data:/data \
  ghcr.io/murdahl/velux-mqtt:latest
```

Keep the `/data` volume. It stores:
- the login token, so the bridge doesn't log in again (and trigger a "new login" email from VELUX) on every restart;
- the window signing key (see below).

## Pairing for roof windows (one time)

Blinds and shutters accept plain commands. **Roof windows only accept commands signed with a key from your gateway.** To get the key:

```sh
docker run --rm -it -e VELUX_USER=… -e VELUX_PASSWORD=… -v velux-data:/data \
  ghcr.io/murdahl/velux-mqtt:latest velux-mqtt pair 192.168.1.20   # the gateway's IP
```

1. The command asks VELUX to put the gateway into pairing mode.
2. When the gateway's light flashes, **press the button on the gateway**.
3. The key is fetched over your local network (TCP 25050) and saved to `/data/signing_key.json`. Restart the bridge afterwards.

Run it from a machine on the same network as the gateway. Signed commands include VELUX's rain override: during rain, VELUX itself limits openings to 50 % and closes the windows again after at most 15 minutes.

## Configuration

| Variable              | Default            | Description |
|-----------------------|--------------------|-------------|
| `VELUX_USER`          | (required)         | VELUX ACTIVE app email |
| `VELUX_PASSWORD`      | (required)         | VELUX ACTIVE app password |
| `MQTT_HOST`           | (required)         | MQTT broker |
| `MQTT_PORT`           | `1883`             | |
| `MQTT_USERNAME`       |                    | |
| `MQTT_PASSWORD`       |                    | |
| `MQTT_TOPIC_PREFIX`   | `velux`            | |
| `POLL_INTERVAL`       | `60`               | Seconds between polls (minimum 10). The bridge polls every 5 s while something moves and right after a command |
| `ENABLE_CONTROL`      | `false`            | `true` to accept commands on `velux/set/...` |
| `TOKEN_FILE`          | `/data/token.json` | Where the login token is kept |
| `SIGNING_KEY_FILE`    | `/data/signing_key.json` | Where the window signing key is kept |
| `LOG_LEVEL`           | `INFO`             | |

## Topics

| Topic           | Retained | Payload |
|-----------------|----------|---------|
| `velux/status`  | yes      | `online` / `offline`. Goes `offline` when the cloud or gateway is unreachable, or the bridge stops |
| `velux/state`   | yes      | JSON, see below |

```json
{
  "gateway_online": true,
  "raining": false,
  "windows_controllable": true,
  "covers": [
    {"id": "5336272614300225", "name": "NV", "type": "window", "room": "Staircase", "bridge": "70:ee:50:…",
     "position": 0, "target_position": 0, "moving": false, "reachable": true, "battery": "high", "vent_position": 7}
  ]
}
```

- **`windows_controllable`:** `false` until the gateway is paired (see above).
- **`vent_position`:** windows only. The ventilation step, where the flap is open and the sash stays locked; VELUX reports it as `secure_position`.

- **`position`:** 0 = closed/down and 100 = open/up, for windows and blinds alike.
- **`raining`:** comes from the gateway's rain sensor.
- **`type`:** `window`, `awning_blind`, `shutter` and so on, as VELUX reports it.

## Control

With `ENABLE_CONTROL=true`:

| Topic                  | Payload |
|------------------------|---------|
| `velux/set/<cover id>` | `open`, `close`, `vent` (windows), `stop` or a position `0`–`100` |
| `velux/set/windows`    | same, for all windows |
| `velux/set/blinds`     | same, for all blinds and shutters |

```sh
mosquitto_pub -t velux/set/blinds -m close
mosquitto_pub -t velux/set/5336272614300225 -m 30
```

`stop` stops every movement on the gateway. The API can't stop a single cover.

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## Credits

- **API endpoints and public client credentials:** from [IngmarStein/ha-velux-active](https://github.com/IngmarStein/ha-velux-active) (Apache-2.0).
- **Window signing and the local gateway pairing protocol:** adapted from [Niek/ha-velux-active](https://github.com/Niek/ha-velux-active) (MIT). The pairing tests check a simulated gateway against that project's client.

This is an unofficial client, and VELUX can change the API at any time.
