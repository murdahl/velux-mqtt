from dataclasses import dataclass

from .model import Cover, House

# Command targets that address a whole group instead of one cover
GROUPS = {"windows": ("window",), "blinds": ("awning_blind", "venetian_blind", "roller_blind", "shutter")}


class CommandError(Exception):
    pass


@dataclass(frozen=True)
class Move:
    covers: tuple[Cover, ...]
    position: int

    def describe(self) -> str:
        return f"{', '.join(cover.name for cover in self.covers)} → {self.position}%"


@dataclass(frozen=True)
class Stop:
    def describe(self) -> str:
        return "stop all movements"


def parse_position(payload: str) -> int | None:
    """open/close/stop or a position 0-100. None means stop."""
    value = payload.strip().lower()
    if value == "open":
        return 100
    if value == "close":
        return 0
    if value == "stop":
        return None
    try:
        position = int(float(value))
    except ValueError:
        raise CommandError(f"Unsupported payload {payload!r}, expected open, close, stop or 0-100") from None
    if not 0 <= position <= 100:
        raise CommandError(f"Position {position} out of range 0-100")
    return position


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
    return Move(covers, position)
