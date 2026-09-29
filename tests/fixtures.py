# Made-up data in the shape of the VELUX ACTIVE API responses (homesdata / homestatus)
GATEWAY = "70:ee:50:00:00:01"
HOME = "5f0000000000000000000001"

HOMES_DATA = {
    "body": {
        "homes": [
            {"id": "other-home", "name": "Cabin", "rooms": [], "modules": []},
            {
                "id": HOME,
                "name": "Home",
                "rooms": [{"id": "1", "name": "Staircase"}],
                "modules": [
                    {"id": GATEWAY, "type": "NXG", "name": "VELUX Gateway"},
                    {"id": "5300000000000001", "type": "NXO", "name": "NV", "room_id": "1", "bridge": GATEWAY, "velux_type": "window"},
                    {"id": "5300000000000002", "type": "NXO", "name": "NH", "room_id": "1", "bridge": GATEWAY, "velux_type": "window"},
                    {"id": "79000001ffffffff", "type": "NXO", "name": "Soverom", "room_id": "1", "bridge": GATEWAY, "velux_type": "awning_blind"},
                ],
            },
        ],
        "user": {"email": "someone@example.com"},
    }
}

HOME_STATUS = {
    "body": {
        "home": {
            "id": HOME,
            "modules": [
                {"id": GATEWAY, "type": "NXG", "is_raining": False, "wifi_state": "full"},
                {"id": "5300000000000001", "type": "NXO", "velux_type": "window", "current_position": 0, "target_position": 0, "reachable": True, "battery_state": "high"},
                {"id": "5300000000000002", "type": "NXO", "velux_type": "window", "current_position": 20, "target_position": 100, "reachable": True, "battery_state": "low"},
                {"id": "79000001ffffffff", "type": "NXO", "velux_type": "awning_blind", "current_position": 0, "target_position": 0, "reachable": False},
            ],
        }
    }
}
