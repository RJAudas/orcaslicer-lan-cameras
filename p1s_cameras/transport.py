"""Read-only P1S LAN camera and status connections."""
import base64
from contextlib import contextmanager
import json
import os
import socket
import ssl
import struct
import time


class Inactive(Exception):
    """The page stopped requesting frames."""


@contextmanager
def connection(settings, port, ca):
    # The printer certificate identifies its serial, even when connecting by numeric LAN IP.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=ca)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw:
        raw.settimeout(3)
        raw.connect((settings["host"], port))
        with context.wrap_socket(raw, server_hostname=settings["serial"]) as stream:
            stream.settimeout(0.25)
            yield stream


class Reader:
    def __init__(self, stream, active):
        self.stream = stream
        self.active = active

    def take(self, size, deadline):
        result = bytearray()
        while len(result) < size:
            if not self.active():
                raise Inactive
            if time.monotonic() >= deadline:
                raise TimeoutError
            try:
                chunk = self.stream.recv(min(65536, size - len(result)))
            except socket.timeout:
                continue
            if not chunk:
                raise EOFError
            result.extend(chunk)
        return bytes(result)


def camera(settings, ca, active, update):
    with connection(settings, 6000, ca) as stream:
        if not active():
            return
        stream.sendall(struct.pack("<4I32s32s", 64, 0x3000, 0, 0, b"bblp", settings["code"].encode("ascii")))
        reader = Reader(stream, active)
        while active():
            deadline = time.monotonic() + 10
            size = struct.unpack_from("<I", reader.take(16, deadline))[0]
            if not 4 <= size <= 2 * 1024 * 1024:
                raise ValueError("Invalid camera frame length")
            frame = reader.take(size, deadline)
            if not frame.startswith(b"\xff\xd8\xff") or not frame.endswith(b"\xff\xd9"):
                raise ValueError("Invalid camera frame")
            update(base64.b64encode(frame).decode("ascii"))


def mqtt_string(value):
    data = value.encode("utf-8")
    return struct.pack(">H", len(data)) + data


def packet(kind, payload):
    length = len(payload)
    header = bytearray([kind])
    while length >= 128:
        header.append(128 | (length % 128))
        length //= 128
    header.append(length)
    return header + payload


def read_packet(reader):
    deadline = time.monotonic() + 25
    kind = reader.take(1, deadline)[0]
    length = 0
    for shift in (0, 7, 14, 21):
        digit = reader.take(1, deadline)[0]
        length |= (digit & 127) << shift
        if length > 1024 * 1024:
            break
        if digit < 128:
            return kind, reader.take(length, deadline)
    raise ValueError("Invalid status packet length")


def status(settings, ca, active, update):
    # MQTT 3.1.1, clean session, QoS 0. The sole publish requests a full read-only status report.
    topic = "device/" + settings["serial"]
    report = mqtt_string(topic + "/report")
    client = mqtt_string("orca-cam-" + os.urandom(6).hex())
    login = client + mqtt_string("bblp") + mqtt_string(settings["code"])
    with connection(settings, 8883, ca) as stream:
        if not active():
            return
        reader = Reader(stream, active)
        stream.sendall(packet(0x10, mqtt_string("MQTT") + b"\x04\xc2\x00\x3c" + login))
        if read_packet(reader) != (0x20, b"\x00\x00"):
            raise ValueError("Status login rejected")
        stream.sendall(packet(0x82, b"\x00\x01" + report + b"\x00"))
        if read_packet(reader) != (0x90, b"\x00\x01\x00"):
            raise ValueError("Status subscription rejected")
        query = b'{"pushing":{"command":"pushall","sequence_id":"0","version":1,"push_target":1}}'
        stream.sendall(packet(0x30, mqtt_string(topic + "/request") + query))
        last_ping = time.monotonic()
        while active():
            kind, payload = read_packet(reader)
            # PUBLISH at QoS 0, optionally with the retained flag.
            if kind in (0x30, 0x31) and payload.startswith(report):
                message = json.loads(payload[len(report):])
                fields = message.get("print") if isinstance(message, dict) else None
                if isinstance(fields, dict) and fields.get("command") == "push_status":
                    update(fields)
            if time.monotonic() - last_ping >= 20:
                stream.sendall(b"\xc0\x00")
                last_ping = time.monotonic()
