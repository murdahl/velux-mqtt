import copy
import json

import httpx
from fixtures import HOME, HOME_STATUS, HOMES_DATA

from velux_mqtt.bridge import EMPTY_ANSWERS_BEFORE_OFFLINE, Bridge
from velux_mqtt.config import Config


class FakeClient:
    def __init__(self) -> None:
        self.status = HOME_STATUS
        self.error: Exception | None = None

    def homes_data(self) -> dict:
        return HOMES_DATA

    def home_status(self, _home_id: str) -> dict:
        if self.error:
            raise self.error
        return self.status


class FakeMqtt:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.published.append((topic, payload))

    def on(self, topic: str) -> list[str]:
        return [payload for published, payload in self.published if published == topic]


def bridge_with(client: FakeClient) -> tuple[Bridge, FakeMqtt]:
    bridge = Bridge(Config(velux_user="u", velux_password="p", mqtt_host="localhost"), client, None)
    bridge.mqtt = FakeMqtt()
    return bridge, bridge.mqtt


def status_without_positions(keep: tuple[str, ...] = ()) -> dict:
    """The answer in the shape the cloud gives right after an outage: the modules are there, the positions aren't"""
    status = copy.deepcopy(HOME_STATUS)
    for module in status["body"]["home"]["modules"]:
        if module["id"] not in keep:
            module.pop("current_position", None)
            module.pop("target_position", None)
    return status


def test_a_normal_answer_is_published_and_the_bridge_is_online():
    client = FakeClient()
    bridge, mqtt = bridge_with(client)

    bridge._poll()

    assert mqtt.on("velux/status") == ["online"]
    assert [cover["position"] for cover in json.loads(mqtt.on("velux/state")[0])["covers"]] == [0, 20, 0]


def test_an_answer_without_positions_publishes_nothing():
    client = FakeClient()
    bridge, mqtt = bridge_with(client)
    bridge._poll()
    mqtt.published.clear()

    client.status = status_without_positions()
    bridge._poll()

    assert mqtt.published == []


def test_publishing_resumes_when_the_positions_are_back():
    client = FakeClient()
    bridge, mqtt = bridge_with(client)
    bridge._poll()
    client.status = status_without_positions()
    bridge._poll()
    mqtt.published.clear()

    client.status = HOME_STATUS
    bridge._poll()

    assert len(mqtt.on("velux/state")) == 1
    assert bridge.empty_answers == 0


def test_a_first_answer_without_positions_does_not_claim_the_bridge_is_online():
    client = FakeClient()
    client.status = status_without_positions()
    bridge, mqtt = bridge_with(client)

    bridge._poll()

    assert mqtt.published == []


def test_many_answers_in_a_row_without_positions_mean_offline():
    client = FakeClient()
    bridge, mqtt = bridge_with(client)
    bridge._poll()
    client.status = status_without_positions()

    for _ in range(EMPTY_ANSWERS_BEFORE_OFFLINE - 1):
        bridge._poll()
    assert mqtt.on("velux/status") == ["online"]

    bridge._poll()
    bridge._poll()
    assert mqtt.on("velux/status") == ["online", "offline"]
    assert len(mqtt.on("velux/state")) == 1  # only the good one

    client.status = HOME_STATUS
    bridge._poll()
    assert mqtt.on("velux/status") == ["online", "offline", "online"]


def test_one_cover_without_a_position_does_not_silence_the_others():
    client = FakeClient()
    client.status = status_without_positions(keep=("5300000000000001", "5300000000000002"))
    bridge, mqtt = bridge_with(client)

    bridge._poll()

    covers = json.loads(mqtt.on("velux/state")[0])["covers"]
    assert [cover["position"] for cover in covers] == [0, 20, None]


def test_a_house_without_covers_is_still_published():
    client = FakeClient()
    client.status = {"body": {"home": {"id": HOME, "modules": []}}}
    bridge, mqtt = bridge_with(client)
    bridge.homes_data = copy.deepcopy(HOMES_DATA)
    bridge.homes_data_at = float("inf")
    bridge.homes_data["body"]["homes"][1]["modules"] = []
    bridge.home_id = HOME

    bridge._poll()

    assert json.loads(mqtt.on("velux/state")[0])["covers"] == []


def test_an_unreachable_cloud_is_still_offline():
    client = FakeClient()
    client.error = httpx.ConnectError("Temporary failure in name resolution")
    bridge, mqtt = bridge_with(client)

    bridge._poll()

    assert mqtt.on("velux/status") == ["offline"]
    assert mqtt.on("velux/state") == []
