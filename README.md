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

Keep the `/data` volume: it stores the login token, so the bridge doesn't log in again (and trigger a "new login" email from VELUX) on every restart.

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
  "covers": [
    {"id": "5336272614300225", "name": "NV", "type": "window", "room": "Staircase", "bridge": "70:ee:50:…",
     "position": 0, "target_position": 0, "moving": false, "reachable": true, "battery": "high"}
  ]
}
```

- **`position`:** 0 = closed/down and 100 = open/up, for windows and blinds alike.
- **`raining`:** comes from the gateway's rain sensor.
- **`type`:** `window`, `awning_blind`, `shutter` and so on, as VELUX reports it.

## Control

With `ENABLE_CONTROL=true`:

| Topic                  | Payload |
|------------------------|---------|
| `velux/set/<cover id>` | `open`, `close`, `stop` or a position `0`–`100` |
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

The API endpoints and the app's public client credentials come from [IngmarStein/ha-velux-active](https://github.com/IngmarStein/ha-velux-active) (Apache-2.0), the Home Assistant integration for VELUX ACTIVE. This is an unofficial client: VELUX can change the API at any time.
