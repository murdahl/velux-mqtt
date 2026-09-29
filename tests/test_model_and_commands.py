import pytest
from fixtures import GATEWAY, HOME, HOME_STATUS, HOMES_DATA

from velux_mqtt.commands import CommandError, Move, Stop, parse_command, parse_position
from velux_mqtt.model import build_house, pick_home_id


@pytest.fixture
def house():
    return build_house(HOMES_DATA, HOME_STATUS, pick_home_id(HOMES_DATA))


def test_picks_home_with_gateway():
    assert pick_home_id(HOMES_DATA) == HOME

    with pytest.raises(LookupError):
        pick_home_id({"body": {"homes": [{"id": "x", "modules": []}]}})


def test_state(house):
    state = house.to_state(windows_controllable=True)

    assert state["gateway_online"] is True
    assert state["raining"] is False
    assert state["covers"][0] == {
        "id": "5300000000000001", "name": "NV", "type": "window", "room": "Staircase", "bridge": GATEWAY,
        "position": 0, "target_position": 0, "reachable": True, "battery": "high", "vent_position": 7, "moving": False,
    }
    assert state["windows_controllable"] is True
    assert state["covers"][1]["moving"] is True
    assert state["covers"][2]["reachable"] is False
    assert house.moving is True
    assert house.bridge_id == GATEWAY


@pytest.mark.parametrize(("payload", "position"), [("open", 100), ("CLOSE", 0), (" 30 ", 30), ("45.6", 45), ("stop", None), ("Vent", "vent")])
def test_parse_position(payload, position):
    assert parse_position(payload) == position


@pytest.mark.parametrize("payload", ["101", "-1", "half", ""])
def test_rejects_bad_positions(payload):
    with pytest.raises(CommandError):
        parse_position(payload)


def test_single_cover(house):
    command = parse_command("5300000000000002", "open", house)

    assert isinstance(command, Move)
    assert [(cover.name, position) for cover, position in command.targets] == [("NH", 100)]


def test_groups(house):
    assert [(cover.name, position) for cover, position in parse_command("windows", "close", house).targets] == [("NV", 0), ("NH", 0)]
    assert [(cover.name, position) for cover, position in parse_command("blinds", "50", house).targets] == [("Soverom", 50)]


def test_vent(house):
    assert [(cover.name, position) for cover, position in parse_command("windows", "vent", house).targets] == [("NV", 7), ("NH", 9)]

    with pytest.raises(CommandError, match="Soverom has no vent position"):
        parse_command("79000001ffffffff", "vent", house)


def test_stop_and_unknown(house):
    assert isinstance(parse_command("windows", "stop", house), Stop)

    with pytest.raises(CommandError, match="Unknown target 'garage'"):
        parse_command("garage", "open", house)
