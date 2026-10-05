import json
import logging
import queue
import time

import httpx
import paho.mqtt.client as mqtt

from .api import TokenStore, VeluxClient, VeluxError
from .commands import CommandError, Move, parse_command
from .config import Config
from .model import House, build_house, pick_home_id
from .signing import Signer, SigningKeyStore

log = logging.getLogger(__name__)

# While covers move (or right after a command), poll often enough to show progress
FAST_POLL_INTERVAL = 5
FAST_POLL_WINDOW = 90
HOMES_DATA_MAX_AGE = 3600  # names and rooms rarely change
# Answers without any positions in a row before the bridge says it is offline; shorter gaps are just skipped
EMPTY_ANSWERS_BEFORE_OFFLINE = 5


class Bridge:
    def __init__(self, config: Config, client: VeluxClient, signer: Signer | None) -> None:
        self.config = config
        self.client = client
        self.signer = signer
        self.status_topic = f"{config.mqtt_topic_prefix}/status"
        self.state_topic = f"{config.mqtt_topic_prefix}/state"
        self.command_prefix = f"{config.mqtt_topic_prefix}/set/"
        # Filled from the MQTT network thread, drained by the main loop, so the API client is only used from one thread
        self.commands: queue.Queue[tuple[str, str]] = queue.Queue()
        self.last_status: str | None = None
        self.home_id: str | None = None
        self.homes_data: dict | None = None
        self.homes_data_at = 0.0
        self.house: House | None = None
        self.empty_answers = 0
        self.mqtt = self._create_mqtt_client()

    def _create_mqtt_client(self) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="velux-mqtt")
        if self.config.mqtt_username:
            client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password)
        client.will_set(self.status_topic, "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = lambda _client, _userdata, _flags, reason, _props: log.warning(
            "MQTT disconnected: %s", reason
        )
        client.on_message = self._on_message
        return client

    def _on_connect(self, client: mqtt.Client, _userdata, _flags, reason, _props) -> None:
        log.info("MQTT connected: %s", reason)
        # Re-publish on reconnect, since the broker may have fired our will in the meantime
        if self.last_status:
            client.publish(self.status_topic, self.last_status, retain=True)
        if self.config.enable_control:
            client.subscribe(f"{self.command_prefix}+")
            log.info("Control enabled, listening on %s+", self.command_prefix)

    def _on_message(self, _client, _userdata, message: mqtt.MQTTMessage) -> None:
        target = message.topic.removeprefix(self.command_prefix)
        self.commands.put((target, message.payload.decode(errors="replace")))

    def _set_status(self, status: str) -> None:
        if status != self.last_status:
            self.mqtt.publish(self.status_topic, status, retain=True)
            self.last_status = status

    def _refresh(self) -> None:
        if self.homes_data is None or time.monotonic() - self.homes_data_at > HOMES_DATA_MAX_AGE:
            self.homes_data = self.client.homes_data()
            self.homes_data_at = time.monotonic()
            self.home_id = pick_home_id(self.homes_data)
        status = self.client.home_status(self.home_id)
        self.house = build_house(self.homes_data, status, self.home_id)

    def _poll(self) -> None:
        try:
            self._refresh()
        except (httpx.HTTPError, VeluxError, LookupError, KeyError) as error:
            log.warning("VELUX ACTIVE unreachable: %s", error)
            self._set_status("offline")
            return

        # Publishing positions the cloud didn't give would read as every cover being closed
        if not self.house.has_positions:
            self.empty_answers += 1
            log.warning("VELUX ACTIVE answered without cover positions (%d in a row), publishing nothing", self.empty_answers)
            if self.empty_answers >= EMPTY_ANSWERS_BEFORE_OFFLINE:
                self._set_status("offline")
            return
        self.empty_answers = 0

        state = self.house.to_state(windows_controllable=self.signer is not None)
        self.mqtt.publish(self.state_topic, json.dumps(state), retain=True)
        self._set_status("online")
        log.debug("Published %s", state)

    def _execute(self, target: str, payload: str) -> None:
        if self.house is None:
            self._poll()
            if self.house is None:
                log.error("Ignoring command for %s: VELUX state unknown", target)
                return
        try:
            command = parse_command(target, payload, self.house)
        except CommandError as error:
            log.warning("Ignoring command: %s", error)
            return

        try:
            if isinstance(command, Move):
                self.client.move(self.house, list(command.targets), self.signer)
            else:
                self.client.stop_all(self.house.home_id, self.house.bridge_id)
        except (httpx.HTTPError, VeluxError) as error:
            log.error("Command %s failed: %s", command.describe(), error)
            return
        log.info("Executed %s", command.describe())

    def run(self) -> None:
        self.mqtt.connect_async(self.config.mqtt_host, self.config.mqtt_port)
        self.mqtt.loop_start()
        next_poll = 0.0
        fast_until = 0.0
        try:
            while True:
                try:
                    target, payload = self.commands.get(timeout=max(0.0, next_poll - time.monotonic()))
                except queue.Empty:
                    self._poll()
                    fast = (self.house is not None and self.house.moving) or time.monotonic() < fast_until
                    next_poll = time.monotonic() + (FAST_POLL_INTERVAL if fast else self.config.poll_interval)
                    continue

                self._execute(target, payload)
                fast_until = time.monotonic() + FAST_POLL_WINDOW
                # Give the gateway a moment before checking progress
                next_poll = time.monotonic() + 2
        finally:
            self.mqtt.publish(self.status_topic, "offline", retain=True).wait_for_publish(timeout=2)
            self.mqtt.loop_stop()
            self.mqtt.disconnect()


def run(config: Config) -> None:
    with httpx.Client(timeout=20, headers={"User-Agent": "velux-mqtt"}) as http:
        client = VeluxClient(
            http,
            config.velux_user,
            config.velux_password,
            config.client_id,
            config.client_secret,
            TokenStore(config.token_file),
        )
        key = SigningKeyStore(config.signing_key_file).load()
        if key is None:
            log.warning("No window signing key; blinds work, windows need `velux-mqtt pair <gateway ip>`")
        Bridge(config, client, Signer(key) if key else None).run()
