import re
from app.utils.definitions import get_proc_hostname
from app.utils.json_connection import create_ssl_connection, send_json, recv_json


def get_device_lock_status_by_area(db, area):
    processor = area.processor
    if not processor:
        return {"status": "error", "message": "Processor not found"}

    try:
        with create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as sock:
            area_code = area.code
            send_json(sock, {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/area/{area_code}/associatedcontrolstation"}
            })
            response = recv_json(sock)
            control_stations = response.get("Body", {}).get("ControlStations", [])

            device_statuses = []

            for station in control_stations:
                for device in station.get("AssociatedGangedDevices", []):
                    device_href = device.get("Device", {}).get("href")
                    if not device_href:
                        continue

                    send_json(sock, {
                        "CommuniqueType": "ReadRequest",
                        "Header": {"Url": f"{device_href}/buttongroup/expanded"}
                    })
                    btn_response = recv_json(sock)
                    btn_groups = btn_response.get("Body", {}).get("ButtonGroupsExpanded", [])

                    for group in btn_groups:
                        for button in group.get("Buttons", []):
                            engraving = button.get("Engraving", {}).get("Text", "").strip().lower()
                            if engraving not in ["lock/unlock", "device lock/unlock"]:
                                continue

                            button_href = button.get("href", "")
                            match = re.search(r"/button/(\d+)", button_href)
                            if not match:
                                continue

                            button_id = int(match.group(1))

                            send_json(sock, {
                                "CommuniqueType": "ReadRequest",
                                "Header": {"Url": f"/button/{button_id}"}
                            })
                            btn_detail = recv_json(sock)
                            led_href = btn_detail.get("Body", {}).get("Button", {}).get("AssociatedLED", {}).get("href", "")
                            if not led_href:
                                continue

                            send_json(sock, {
                                "CommuniqueType": "ReadRequest",
                                "Header": {"Url": f"{led_href}/status"}
                            })
                            led_resp = recv_json(sock)
                            state = led_resp.get("Body", {}).get("LEDStatus", {}).get("State", "Unknown")

                            device_statuses.append({
                                "button_id": button_id,
                                "status": "Locked" if state == "On" else "Unlocked" if state == "Off" else "Unknown"
                            })

    except Exception as e:
        return {"status": "error", "message": f"Processor communication failed: {e}"}

    return {"status": "success", "devices": device_statuses}


def toggle_device_lock_by_button(db, area, buttoncode: int):
    processor = area.processor
    if not processor:
        return {"status": "error", "message": "Processor not found"}

    try:
        with create_ssl_connection(processor.ipv4, processor.mac, processor.system, processor_ipv4=processor.ipv4) as sock:
            send_json(sock, {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"/button/{buttoncode}"}
            })
            btn_detail = recv_json(sock)
            button_data = btn_detail.get("Body", {}).get("Button", {})

            engraving = button_data.get("Engraving", {}).get("Text", "").strip().lower()
            if engraving not in ["lock/unlock", "device lock/unlock"]:
                return {
                    "status": "error",
                    "message": "Invalid button. Only Lock/Unlock types allowed."
                }

            send_json(sock, {
                "CommuniqueType": "CreateRequest",
                "Header": {"Url": f"/button/{buttoncode}/commandprocessor"},
                "Body": {
                    "Command": {"CommandType": "PressAndRelease"}
                }
            })
            _ = recv_json(sock)

            led_href = button_data.get("AssociatedLED", {}).get("href", "")
            if not led_href:
                return {
                    "status": "error",
                    "message": "Associated LED not found for button"
                }

            send_json(sock, {
                "CommuniqueType": "ReadRequest",
                "Header": {"Url": f"{led_href}/status"}
            })
            led_resp = recv_json(sock)
            state = led_resp.get("Body", {}).get("LEDStatus", {}).get("State", "Unknown")

            return {
                "status": "success",
                "devices": [{
                    "button_id": buttoncode,
                    "status": "Locked" if state == "On" else "Unlocked" if state == "Off" else "Unknown"
                }]
            }

    except Exception as e:
        return {"status": "error", "message": f"Processor communication failed: {e}"}