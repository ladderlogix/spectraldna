"""
SpectralDNA centralized lookup tables.

All embedded as Python dicts — no network calls, no external files.
Covers OUI (MAC manufacturer), BLE company IDs, BLE service UUIDs,
LTE EARFCN-to-carrier mapping, 5G NR frequency-to-carrier mapping,
and ANT+ device type codes.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# OUI Database — top ~200 consumer electronics manufacturers
# Key: first 3 octets uppercase "AA:BB:CC", Value: company name
# ---------------------------------------------------------------------------

OUI_DATABASE: dict[str, str] = {
    # Apple
    "AC:DE:48": "Apple, Inc.",
    "3C:22:FB": "Apple, Inc.",
    "A4:83:E7": "Apple, Inc.",
    "28:6A:BA": "Apple, Inc.",
    "F0:18:98": "Apple, Inc.",
    "DC:A9:04": "Apple, Inc.",
    "8C:85:90": "Apple, Inc.",
    "F4:5C:89": "Apple, Inc.",
    "60:F8:1D": "Apple, Inc.",
    "70:DE:E2": "Apple, Inc.",
    "78:7B:8A": "Apple, Inc.",
    "BC:52:B7": "Apple, Inc.",
    "B0:34:95": "Apple, Inc.",
    "48:A1:95": "Apple, Inc.",
    "14:7D:DA": "Apple, Inc.",
    "A8:5C:2C": "Apple, Inc.",
    "D0:03:4B": "Apple, Inc.",
    "C0:B6:58": "Apple, Inc.",
    "38:F9:D3": "Apple, Inc.",
    "A0:99:9B": "Apple, Inc.",
    # Samsung
    "8C:F5:A3": "Samsung Electronics",
    "50:01:BB": "Samsung Electronics",
    "AC:5F:3E": "Samsung Electronics",
    "CC:07:AB": "Samsung Electronics",
    "34:14:5F": "Samsung Electronics",
    "E4:7D:BD": "Samsung Electronics",
    "B0:72:BF": "Samsung Electronics",
    "BC:14:EF": "Samsung Electronics",
    "10:D5:61": "Samsung Electronics",
    "78:AB:BB": "Samsung Electronics",
    "94:35:0A": "Samsung Electronics",
    "C0:BD:C8": "Samsung Electronics",
    "D0:87:E2": "Samsung Electronics",
    "F8:04:2E": "Samsung Electronics",
    "28:CC:01": "Samsung Electronics",
    # Google / Nest
    "F4:F5:D8": "Google, Inc.",
    "54:60:09": "Google, Inc.",
    "A4:77:33": "Google, Inc.",
    "30:FD:38": "Google, Inc.",
    "48:D6:D5": "Google, Inc.",
    "E8:98:6D": "Google, Inc.",
    "20:DF:B9": "Google, Inc.",
    "18:D6:C7": "Google Nest",
    "64:16:66": "Google Nest",
    # Intel
    "8C:EC:4B": "Intel Corporate",
    "48:51:B7": "Intel Corporate",
    "7C:B2:7D": "Intel Corporate",
    "B4:69:21": "Intel Corporate",
    "34:13:E8": "Intel Corporate",
    "3C:58:C2": "Intel Corporate",
    "A4:C3:F0": "Intel Corporate",
    "40:74:E0": "Intel Corporate",
    "C8:E2:65": "Intel Corporate",
    "DC:71:96": "Intel Corporate",
    # Qualcomm / Atheros
    "00:03:7F": "Atheros Communications",
    "04:F0:21": "Qualcomm Inc.",
    "9C:F6:DD": "Qualcomm Inc.",
    # Broadcom
    "20:10:7A": "Broadcom Inc.",
    "00:10:18": "Broadcom Inc.",
    # Realtek
    "00:E0:4C": "Realtek Semiconductor",
    "48:5B:39": "Realtek Semiconductor",
    "52:54:00": "Realtek (QEMU/KVM)",
    "80:26:89": "Realtek Semiconductor",
    # MediaTek
    "00:0C:E7": "MediaTek Inc.",
    "24:0D:C2": "MediaTek Inc.",
    # Huawei
    "FC:48:EF": "Huawei Technologies",
    "48:46:FB": "Huawei Technologies",
    "24:09:95": "Huawei Technologies",
    "70:8C:B6": "Huawei Technologies",
    "88:66:A5": "Huawei Technologies",
    "CC:A2:23": "Huawei Technologies",
    "10:44:00": "Huawei Technologies",
    "84:A8:E4": "Huawei Technologies",
    # Xiaomi
    "78:11:DC": "Xiaomi Communications",
    "64:CC:2E": "Xiaomi Communications",
    "28:6C:07": "Xiaomi Communications",
    "AC:C1:EE": "Xiaomi Communications",
    "34:80:B3": "Xiaomi Communications",
    "50:64:2B": "Xiaomi Communications",
    "7C:1C:4E": "Xiaomi Communications",
    # TP-Link
    "50:C7:BF": "TP-Link Technologies",
    "C0:06:C3": "TP-Link Technologies",
    "E8:48:B8": "TP-Link Technologies",
    "B0:95:75": "TP-Link Technologies",
    "14:EB:B6": "TP-Link Technologies",
    "60:32:B1": "TP-Link Technologies",
    # Espressif (ESP32/ESP8266)
    "24:6F:28": "Espressif Inc.",
    "30:AE:A4": "Espressif Inc.",
    "AC:67:B2": "Espressif Inc.",
    "84:CC:A8": "Espressif Inc.",
    "7C:DF:A1": "Espressif Inc.",
    "3C:61:05": "Espressif Inc.",
    "A4:CF:12": "Espressif Inc.",
    # Amazon
    "F0:D2:F1": "Amazon Technologies",
    "74:C2:46": "Amazon Technologies",
    "A0:02:DC": "Amazon Technologies",
    "FC:65:DE": "Amazon Technologies",
    "44:00:49": "Amazon Technologies",
    "B4:7C:9C": "Amazon Technologies",
    # Microsoft / Xbox
    "7C:ED:8D": "Microsoft Corporation",
    "28:18:78": "Microsoft Corporation",
    "00:50:F2": "Microsoft Corporation",
    "C8:3F:26": "Microsoft Corporation",
    # Sony
    "FC:F1:52": "Sony Corporation",
    "04:5D:4B": "Sony Corporation",
    "AC:9B:0A": "Sony Corporation",
    "78:84:3C": "Sony Corporation",
    "04:76:6E": "Sony Interactive Entertainment",
    # LG
    "88:C9:D0": "LG Electronics",
    "10:68:3F": "LG Electronics",
    "C4:36:6C": "LG Electronics",
    "CC:FA:00": "LG Electronics",
    # OnePlus / OPPO / BBK
    "C0:EE:FB": "OnePlus Technology",
    "94:65:2D": "OnePlus Technology",
    "64:A2:F9": "OnePlus Technology",
    "A4:3B:FA": "OPPO Electronics",
    # Motorola / Lenovo
    "3C:5A:B4": "Motorola (Lenovo)",
    "9C:D9:17": "Motorola Mobility",
    "C8:14:79": "Motorola Mobility",
    # Cisco / Linksys / Meraki
    "00:17:94": "Cisco Systems",
    "34:56:FE": "Cisco Meraki",
    "00:18:0A": "Cisco Meraki",
    "E8:65:49": "Cisco Meraki",
    "AC:17:C8": "Cisco Meraki",
    # Aruba / HPE
    "00:0B:86": "Aruba Networks (HPE)",
    "24:DE:C6": "Aruba Networks (HPE)",
    "9C:1C:12": "Aruba Networks (HPE)",
    # Ubiquiti
    "18:E8:29": "Ubiquiti Inc.",
    "24:5A:4C": "Ubiquiti Inc.",
    "68:D7:9A": "Ubiquiti Inc.",
    "F4:92:BF": "Ubiquiti Inc.",
    "FC:EC:DA": "Ubiquiti Inc.",
    # Netgear
    "A0:04:60": "Netgear Inc.",
    "C4:04:15": "Netgear Inc.",
    "E4:F4:C6": "Netgear Inc.",
    "94:B4:0F": "Netgear Inc.",
    # ASUS
    "04:D4:C4": "ASUSTek Computer",
    "F4:6D:04": "ASUSTek Computer",
    "2C:FD:A1": "ASUSTek Computer",
    "1C:87:2C": "ASUSTek Computer",
    # Dell
    "B8:AC:6F": "Dell Inc.",
    "18:03:73": "Dell Inc.",
    "F8:BC:12": "Dell Inc.",
    # HP
    "64:51:06": "Hewlett Packard",
    "10:1F:74": "Hewlett Packard",
    "3C:D9:2B": "Hewlett Packard Enterprise",
    # Raspberry Pi
    "B8:27:EB": "Raspberry Pi Foundation",
    "DC:A6:32": "Raspberry Pi Trading",
    "E4:5F:01": "Raspberry Pi Trading",
    # Nordic / nRF (BLE peripherals)
    "E5:A4:B1": "Nordic Semiconductor",
    "F5:78:6E": "Nordic Semiconductor",
    # Garmin
    "C0:D0:12": "Garmin International",
    "00:1C:B3": "Garmin International",
    # Fitbit
    "C8:FF:28": "Fitbit, Inc.",
    # Bose
    "04:52:C7": "Bose Corporation",
    "2C:41:A1": "Bose Corporation",
    "60:AB:D2": "Bose Corporation",
    # JBL / Harman
    "00:02:5B": "Harman International",
    # Roku
    "DC:3A:5E": "Roku, Inc.",
    "B0:A7:37": "Roku, Inc.",
    "C8:3A:6B": "Roku, Inc.",
    # Ring (Amazon)
    "34:3E:A4": "Ring (Amazon)",
    # Sonos
    "78:28:CA": "Sonos, Inc.",
    "B8:E9:37": "Sonos, Inc.",
    "54:2A:1B": "Sonos, Inc.",
    # Wyze
    "2C:AA:8E": "Wyze Labs",
    # Tesla
    "4C:FC:AA": "Tesla Motors",
    # Tile tracker
    "C4:AC:05": "Tile, Inc.",
    # Chipolo
    "E7:60:46": "Chipolo",
    # Tuya / Smart Home
    "D8:1F:12": "Tuya Smart",
    "10:D5:61": "Tuya Smart",
}


# ---------------------------------------------------------------------------
# BLE Company Identifiers (Bluetooth SIG assigned numbers)
# Key: 16-bit company ID, Value: company name
# ---------------------------------------------------------------------------

BLE_COMPANY_IDS: dict[int, str] = {
    0x0001: "Nokia Mobile Phones",
    0x0002: "Intel Corp.",
    0x0003: "IBM Corp.",
    0x0004: "Toshiba Corp.",
    0x0006: "Microsoft",
    0x000A: "Qualcomm",
    0x000D: "Texas Instruments",
    0x000F: "Broadcom",
    0x0013: "Atmel",
    0x001D: "Qualcomm Technologies",
    0x004C: "Apple, Inc.",
    0x0059: "Nordic Semiconductor",
    0x0075: "Samsung Electronics",
    0x0087: "Garmin International",
    0x008A: "Plantronics (Poly)",
    0x009E: "Bose Corporation",
    0x00E0: "Google",
    0x00D2: "Dialog Semiconductor",
    0x00FF: "Polar Electro",
    0x0106: "Wahoo Fitness",
    0x010D: "Suunto",
    0x0131: "Tile, Inc.",
    0x0157: "Jabra (GN Audio)",
    0x0171: "Amazon",
    0x0172: "Ring (Amazon)",
    0x0180: "Meta Platforms",
    0x019A: "Fitbit, Inc.",
    0x01B7: "Shenzhen Goodix",
    0x01C1: "Sonos, Inc.",
    0x01DA: "HUAWEI Technologies",
    0x0201: "Amazfit (Huami)",
    0x0224: "Oura Health",
    0x022B: "Xiaomi Inc.",
    0x0262: "Peloton Interactive",
    0x0269: "TrainerRoad",
    0x0279: "Withings",
    0x02FF: "Fitbit",
    0x038F: "Garmin International",
    0x039B: "Therabody",
    0x058E: "JBL (Harman)",
    0x0583: "Philips",
    0x059A: "Sony Corporation",
    0x05A7: "Espressif Systems",
    0x0600: "Skullcandy",
    0x0652: "Beats Electronics",
    0x069C: "Whoop, Inc.",
    0x0822: "Chipolo",
}


# ---------------------------------------------------------------------------
# BLE GATT Service UUIDs (16-bit short form)
# ---------------------------------------------------------------------------

BLE_SERVICE_UUIDS: dict[int, str] = {
    0x1800: "Generic Access",
    0x1801: "Generic Attribute",
    0x1802: "Immediate Alert",
    0x1803: "Link Loss",
    0x1804: "Tx Power",
    0x1805: "Current Time",
    0x1808: "Glucose",
    0x1809: "Health Thermometer",
    0x180A: "Device Information",
    0x180D: "Heart Rate",
    0x180E: "Phone Alert Status",
    0x180F: "Battery Service",
    0x1810: "Blood Pressure",
    0x1811: "Alert Notification",
    0x1812: "Human Interface Device",
    0x1813: "Scan Parameters",
    0x1814: "Running Speed and Cadence",
    0x1816: "Cycling Speed and Cadence",
    0x1818: "Cycling Power",
    0x181A: "Environmental Sensing",
    0x181B: "Body Composition",
    0x181C: "User Data",
    0x181D: "Weight Scale",
    0x181E: "Bond Management",
    0x1820: "Internet Protocol Support",
    0x1822: "Pulse Oximeter",
    0x1826: "Fitness Machine",
    0x1827: "Mesh Provisioning",
    0x1828: "Mesh Proxy",
    0xFE2C: "Google Fast Pair",
    0xFD6F: "Apple Exposure Notification",
}


# ---------------------------------------------------------------------------
# LTE EARFCN to Carrier Mapping (US carriers)
# Each entry: (earfcn_low, earfcn_high, carrier_name, band_label)
# ---------------------------------------------------------------------------

LTE_EARFCN_CARRIERS: list[tuple[int, int, str, str]] = [
    # T-Mobile
    (600, 749, "T-Mobile", "B2"),        # Band 2 (1900 MHz PCS)
    (1950, 2399, "T-Mobile", "B4"),      # Band 4 (AWS-1)
    (5035, 5054, "T-Mobile", "B12"),     # Band 12 (700a) — limited
    (39650, 41589, "T-Mobile", "B41"),   # Band 41 (2.5 GHz TDD)
    (66436, 67335, "T-Mobile", "B66"),   # Band 66 (AWS-3)
    (68586, 68935, "T-Mobile", "B71"),   # Band 71 (600 MHz)
    # AT&T
    (750, 849, "AT&T", "B2"),            # Band 2
    (2400, 2649, "AT&T", "B4"),          # Band 4
    (2750, 2799, "AT&T", "B5"),          # Band 5 (850 MHz)
    (5055, 5089, "AT&T", "B12"),         # Band 12
    (5180, 5279, "AT&T", "B14"),         # Band 14 (FirstNet)
    (9770, 9869, "AT&T", "B30"),         # Band 30 (2.3 GHz)
    (67336, 67535, "AT&T", "B66"),       # Band 66
    # Verizon
    (850, 999, "Verizon", "B2"),         # Band 2
    (2000, 2099, "Verizon", "B4"),       # Band 4
    (2649, 2749, "Verizon", "B5"),       # Band 5
    (5090, 5179, "Verizon", "B13"),      # Band 13 (700c)
    (55240, 56739, "Verizon", "B48"),    # Band 48 (CBRS)
    (66636, 66935, "Verizon", "B66"),    # Band 66
    # Dish Network
    (9210, 9309, "Dish Network", "B29"), # Band 29 (700d SDL)
    (68936, 69035, "Dish Network", "B70"), # Band 70
    # US Cellular
    (1000, 1199, "US Cellular", "B2"),   # Band 2
    (5010, 5034, "US Cellular", "B12"),  # Band 12
]


# ---------------------------------------------------------------------------
# 5G NR Frequency to Carrier Mapping (US deployments)
# Each entry: (freq_low_mhz, freq_high_mhz, carrier_name, band_label)
# ---------------------------------------------------------------------------

NR_FREQ_CARRIERS: list[tuple[float, float, str, str]] = [
    # T-Mobile — n41 (2.5 GHz TDD), n71 (600 MHz), n77 (C-Band)
    (2496.0, 2690.0, "T-Mobile", "n41"),
    (617.0, 652.0, "T-Mobile", "n71"),
    (3700.0, 3800.0, "T-Mobile", "n77"),
    # AT&T — n77 (C-Band), n5 (850 MHz)
    (3550.0, 3700.0, "AT&T", "n77"),
    (869.0, 894.0, "AT&T", "n5"),
    # Verizon — n77 (C-Band), n261 (mmWave 28 GHz)
    (3800.0, 3980.0, "Verizon", "n77"),
    (3300.0, 3550.0, "Verizon", "n78"),
    # Dish Network — n70
    (1695.0, 1710.0, "Dish Network", "n70"),
    # Generic n77/n78 fallback
    (3300.0, 4200.0, "Unknown C-Band Operator", "n77/n78"),
]


# ---------------------------------------------------------------------------
# ANT+ Device Type Codes
# ---------------------------------------------------------------------------

ANT_DEVICE_TYPES: dict[int, str] = {
    0x01: "Bike Speed Sensor",
    0x05: "Environment Sensor",
    0x07: "Light Control",
    0x0B: "Bike Power Sensor",
    0x0F: "Environment",
    0x10: "Multi-Sport Speed & Distance",
    0x11: "Fitness Equipment",
    0x12: "Weight Scale",
    0x19: "Multi-Sport Speed & Distance Monitor",
    0x22: "Geocache",
    0x23: "Shifting (Di2/eTap)",
    0x28: "Suspension",
    0x78: "Heart Rate Monitor",
    0x79: "Speed & Cadence Sensor",
    0x7A: "Cadence Sensor",
    0x7B: "Speed Sensor",
    0x7C: "Stride-Based Speed & Distance",
    0x7D: "Environment Sensor (Legacy)",
    0x0C: "Audio Control",
    0x23: "Shifting System",
    0x30: "Muscle Oxygen Monitor",
    0x33: "Bike Radar",
    0x35: "Bike Lights",
    0x3A: "Tracker",
    0x3E: "Dropper Seatpost",
}


# ---------------------------------------------------------------------------
# Lookup functions
# ---------------------------------------------------------------------------

def lookup_oui(mac: str) -> str:
    """
    Look up manufacturer from MAC address OUI (first 3 octets).

    Parameters
    ----------
    mac : str
        MAC address in "AA:BB:CC:DD:EE:FF" or "AA:BB:CC" format.

    Returns
    -------
    str
        Manufacturer name, or "" if not found.
    """
    oui = mac[:8].upper()
    return OUI_DATABASE.get(oui, "")


def lookup_ble_company(company_id: int) -> str:
    """
    Look up BLE company name from Bluetooth SIG company identifier.

    Returns company name or "" if not found.
    """
    return BLE_COMPANY_IDS.get(company_id, "")


def lookup_ble_service(uuid16: int) -> str:
    """
    Look up BLE GATT service name from 16-bit UUID.

    Parameters
    ----------
    uuid16 : int
        16-bit service UUID (e.g., 0x180D for Heart Rate).

    Returns
    -------
    str
        Service name or "" if not found.
    """
    return BLE_SERVICE_UUIDS.get(uuid16, "")


def lookup_lte_carrier(earfcn: int) -> str:
    """
    Look up US LTE carrier from EARFCN.

    Returns "Carrier (Band)" string or "" if not found.
    """
    for low, high, carrier, band in LTE_EARFCN_CARRIERS:
        if low <= earfcn <= high:
            return f"{carrier} ({band})"
    return ""


def lookup_5g_carrier(freq_mhz: float) -> str:
    """
    Look up US 5G NR carrier from center frequency in MHz.

    Returns "Carrier (Band)" string or "" if not found.
    Checks specific carriers first, then falls back to generic.
    """
    # Check specific carriers first (exclude the generic fallback)
    for low, high, carrier, band in NR_FREQ_CARRIERS[:-1]:
        if low <= freq_mhz <= high:
            return f"{carrier} ({band})"
    # Generic fallback
    low, high, carrier, band = NR_FREQ_CARRIERS[-1]
    if low <= freq_mhz <= high:
        return f"{carrier} ({band})"
    return ""


def lookup_ant_device_type(code: int) -> str:
    """
    Look up ANT+ device type name from 8-bit device type code.

    Returns device type name or "" if not found.
    """
    return ANT_DEVICE_TYPES.get(code, "")


def is_mac_randomized(mac: str) -> bool:
    """
    Check if a MAC address is locally administered (randomized).

    The locally-administered bit is bit 1 of the first octet.
    If set, the MAC is random / privacy-rotated.

    Parameters
    ----------
    mac : str
        MAC address in "AA:BB:CC:DD:EE:FF" format.

    Returns
    -------
    bool
        True if the MAC is locally administered (randomized).
    """
    try:
        first_octet = int(mac.split(":")[0], 16)
        return bool(first_octet & 0x02)
    except (ValueError, IndexError):
        return False
