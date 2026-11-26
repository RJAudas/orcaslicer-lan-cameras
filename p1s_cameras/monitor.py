"""Visible-page sessions. Workers publish only into the session that created them."""
import ssl
import threading
import time

from . import transport


LEASE_SECONDS = 3
STATUS_SECONDS = 30
FIELDS = ("subtask_id", "subtask_name", "gcode_file", "gcode_state", "mc_percent", "mc_remaining_time")


def integer(value, maximum):
    if type(value) is int or (isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 6):
        number = int(value)
        if 0 <= number <= maximum:
            return number
    return None


def job_summary(fields):
    parts = []
    for key in ("subtask_name", "gcode_file"):
        name = fields.get(key)
        if isinstance(name, str) and name.strip():
            parts.append(name[:180])
            break
    state = fields.get("gcode_state")
    if isinstance(state, str) and state:
        parts.append(state[:32])
    percent = integer(fields.get("mc_percent"), 100)
    minutes = integer(fields.get("mc_remaining_time"), 99999)
    if percent is not None:
        parts.append(f"{percent}%")
    if minutes is not None:
        hours, minutes = divmod(minutes, 60)
        duration = f"{hours} h {minutes} min" if hours else f"{minutes} min"
        parts.append(f"{duration} remaining")
    return " · ".join(parts) or "Waiting for print status"


class Tile:
    def __init__(self, name):
        self.name = name
        self.state = "Connecting"
        self.jpeg = None
        self.revision = 0
        self.fields = {}
        self.last_report = 0
        self.lock = threading.Lock()

    def frame(self, jpeg):
        with self.lock:
            self.jpeg = jpeg
            self.revision += 1
            self.state = "Live"

    def report(self, delta):
        with self.lock:
            new_job = any(self.fields.get(k) != delta[k] for k in ("subtask_id", "subtask_name", "gcode_file")
                          if k in delta and k in self.fields)
            if new_job or delta.get("msg") == 0:
                self.fields.clear()
            self.fields.update((key, delta[key]) for key in FIELDS if key in delta)
            self.last_report = time.monotonic()

    def snapshot(self, seen):
        with self.lock:
            fresh = self.last_report and time.monotonic() - self.last_report < STATUS_SECONDS
            result = dict(name=self.name, state=self.state, revision=self.revision,
                          info=job_summary(self.fields) if fresh else "Print status unavailable")
            if self.jpeg and seen not in (-1, self.revision):
                result["jpeg"] = self.jpeg
            return result


class Session:
    def __init__(self, printers, ca, camera=transport.camera, status=transport.status):
        self.deadline = time.monotonic() + LEASE_SECONDS
        self.stop = threading.Event()
        self.threads = []
        self.tiles = [Tile(printer["name"]) for printer in printers]
        try:
            for printer, tile in zip(printers, self.tiles):
                if printer.get("connection_error"):
                    tile.state = printer["connection_error"]
                    continue
                for read, is_camera in ((camera, True), (status, False)):
                    worker = threading.Thread(target=self._run, args=(read, printer, ca, tile, is_camera),
                                              name="P1S camera" if is_camera else "P1S report", daemon=True)
                    worker.start()
                    self.threads.append(worker)
        except BaseException:
            self.close()
            raise

    def active(self):
        return not self.stop.is_set() and time.monotonic() < self.deadline

    def renew(self):
        if not self.active():
            return False
        self.deadline = time.monotonic() + LEASE_SECONDS
        return True

    def close(self):
        self.stop.set()

    def join(self):
        for worker in self.threads:
            worker.join()

    def _run(self, read, printer, ca, tile, is_camera):
        delay = 1
        while self.active():
            before = tile.revision if is_camera else tile.last_report
            try:
                read(printer, ca, self.active, tile.frame if is_camera else tile.report)
                if not self.active():
                    return
                reason = "Retrying"
            except transport.Inactive:
                return
            except ssl.SSLCertVerificationError:
                reason = "Check printer serial and certificate"
            except PermissionError:
                reason = "Permission required"
            except (EOFError, OSError, ValueError):
                reason = "Retrying"
            with tile.lock:
                received = (tile.revision if is_camera else tile.last_report) != before
                if is_camera:
                    tile.state = reason
                else:
                    tile.fields.clear()
                    tile.last_report = 0
            if reason != "Retrying":
                return
            if received:
                delay = 1
            until = time.monotonic() + delay
            while self.active() and time.monotonic() < until:
                self.stop.wait(min(0.25, max(0, until - time.monotonic())))
            delay = min(delay * 2, 16)
