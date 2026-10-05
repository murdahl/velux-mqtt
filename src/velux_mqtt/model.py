from dataclasses import asdict, dataclass

COVER_TYPE = "NXO"
GATEWAY_TYPE = "NXG"


@dataclass(frozen=True)
class Cover:
    id: str
    name: str
    type: str  # window, awning_blind, shutter, venetian_blind, ...
    room: str | None
    bridge: str
    position: int | None  # 0 = closed/down, 100 = open/up
    target_position: int | None
    reachable: bool
    battery: str | None
    # Windows: the ventilation position (flap open, sash locked), reported as secure_position
    vent_position: int | None = None

    @property
    def is_window(self) -> bool:
        return self.type == "window"

    @property
    def moving(self) -> bool:
        return self.position is not None and self.target_position is not None and self.position != self.target_position


@dataclass(frozen=True)
class House:
    home_id: str
    bridge_id: str | None
    gateway_online: bool
    raining: bool | None
    covers: tuple[Cover, ...]
    timezone: str | None = None

    @property
    def moving(self) -> bool:
        return any(cover.moving for cover in self.covers)

    @property
    def has_positions(self) -> bool:
        """False when the cloud answered without a position for a single cover, which means it has no contact with the house right now"""
        return not self.covers or any(cover.position is not None for cover in self.covers)

    def to_state(self, windows_controllable: bool) -> dict:
        return {
            "gateway_online": self.gateway_online,
            "raining": self.raining,
            "windows_controllable": windows_controllable,
            "covers": [asdict(cover) | {"moving": cover.moving} for cover in self.covers],
        }


def build_house(homes_data: dict, home_status: dict, home_id: str) -> House:
    home = next(home for home in homes_data["body"]["homes"] if home["id"] == home_id)
    rooms = {room["id"]: room.get("name") for room in home.get("rooms", [])}
    names = {module["id"]: module for module in home.get("modules", [])}
    statuses = {module["id"]: module for module in home_status["body"]["home"].get("modules", [])}

    gateway = next((module for module in statuses.values() if module.get("type") == GATEWAY_TYPE), None)
    covers = []
    for module_id, info in names.items():
        if info.get("type") != COVER_TYPE:
            continue
        status = statuses.get(module_id, {})
        covers.append(
            Cover(
                id=module_id,
                name=info.get("name") or module_id,
                type=status.get("velux_type") or info.get("velux_type") or "unknown",
                room=rooms.get(info.get("room_id")),
                bridge=info.get("bridge") or (gateway or {}).get("id", ""),
                position=status.get("current_position"),
                target_position=status.get("target_position"),
                reachable=bool(status.get("reachable", False)),
                battery=status.get("battery_state"),
                vent_position=status.get("secure_position"),
            )
        )

    return House(
        home_id=home_id,
        bridge_id=gateway["id"] if gateway else None,
        gateway_online=gateway is not None and gateway.get("wifi_state") != "offline",
        raining=gateway.get("is_raining") if gateway else None,
        covers=tuple(covers),
        timezone=home.get("timezone"),
    )


def pick_home_id(homes_data: dict) -> str:
    """The first home that has a VELUX gateway."""
    for home in homes_data["body"]["homes"]:
        if any(module.get("type") == GATEWAY_TYPE for module in home.get("modules", [])):
            return home["id"]
    raise LookupError("No home with a VELUX gateway on this account")
