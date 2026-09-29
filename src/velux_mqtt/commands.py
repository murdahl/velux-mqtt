from dataclasses import dataclass

from .model import Cover, House

# Command targets that address a whole group instead of one cover
GROUPS = {"windows": ("window",), "blinds": ("awning_blind", "venetian_blind", "roller_blind", "shutter")}
VENT = "vent"


class CommandError(Exception):
    pass


@dataclass(frozen=True)
class Move:
    targets: tuple[tuple[Cover, int], ...]

    def describe(self) -> str:
        return ", ".join(f"{cover.name} → {position}%" for cover, position in self.targets)


@dataclass(frozen=True)
class Stop:
    def describe(self) -> str:
        return "stop all movements"


def parse_position(payload: str) -> int | str | None:
    """open/close/vent/stop or a position 0-100. None means stop."""
    value = payload.strip().lower()
    if value == "open":
        return 100
    if value == "close":
        return 0
    if value in ("stop", VENT):
        return None if value == "stop" else VENT
    try:
        position = int(float(value))
    except ValueError:
        raise CommandError(f"Unsupported payload {payload!r}, expected open, close, vent, stop or 0-100") from None
    if not 0 <= position <= 100:
        raise CommandError(f"Position {position} out of range 0-100")
    return position


def _vent_position(cover: Cover) -> int:
    if not cover.is_window or cover.vent_position is None:
        raise CommandError(f"{cover.name} has no vent position")
    return cover.vent_position


def parse_command(target: str, payload: str, house: House) -> Move | Stop:
    position = parse_position(payload)
    if position is None:
        # The API can only stop every movement on the gateway at once
        return Stop()

    if target in GROUPS:
        covers = tuple(cover for cover in house.covers if cover.type in GROUPS[target])
    else:
        covers = tuple(cover for cover in house.covers if cover.id == target)
    if not covers:
        known = ", ".join([*GROUPS, *(cover.id for cover in house.covers)])
        raise CommandError(f"Unknown target {target!r}, expected one of: {known}")

    if position == VENT:
        return Move(tuple((cover, _vent_position(cover)) for cover in covers))
    return Move(tuple((cover, position) for cover in covers))
