import hashlib
from ipaddress import IPv4Address
import json
from pathlib import Path
import re


PRINTER_FILE = Path.home() / ".orca-p1s-cameras.json"


def validate(printers):
    if not isinstance(printers, list):
        raise ValueError("Expected a list of printers")
    hosts, serials = set(), set()
    for printer in printers:
        if not isinstance(printer, dict) or not all(
            isinstance(printer.get(k), str) for k in ("name", "host", "serial", "code")
        ):
            raise ValueError("Missing printer settings")
        if not printer["name"].strip() or len(printer["name"]) > 80:
            raise ValueError("Use a display name of 1 to 80 characters")
        try:
            host = str(IPv4Address(printer["host"]))
        except ValueError:
            raise ValueError("Enter a numeric LAN IPv4 address") from None
        if host in hosts:
            raise ValueError("Each printer needs a different address")
        if not re.fullmatch(r"[A-Za-z0-9]{8,32}", printer["serial"]):
            raise ValueError("Invalid printer serial number")
        serial = printer["serial"].upper()
        if serial in serials:
            raise ValueError("Each printer needs a different serial number")
        if not re.fullmatch(r"[!-~]{1,32}", printer["code"]):
            raise ValueError("Invalid LAN access code")
        hosts.add(host)
        serials.add(serial)


def read_printers(path):
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    validate(rows)
    return rows


def camera_id(printer):
    # Keep raw serials and addresses out of the WebView and Orca's capability config.
    return hashlib.sha256(printer["serial"].upper().encode("ascii")).hexdigest()
