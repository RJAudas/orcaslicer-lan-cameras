from html import escape
import json
from pathlib import Path
from secrets import token_hex
import socket
import threading
import time

import orca

from .monitor import LEASE_SECONDS, Session
from .settings import PRINTER_FILE, camera_id, read_printers


ASSETS = Path(__file__).parent


def printer_roster():
    try:
        return read_printers(PRINTER_FILE), ""
    except (OSError, ValueError):
        return [], "Unable to read printer settings. Check the file permission or open Camera Printer Setup."


def resolve_printer(printer):
    # Resolve once per selected printer on the audited Script invocation. Orca can
    # ask for this exact host before either reader creates its first socket.
    try:
        addresses = socket.getaddrinfo(printer["host"], 0, socket.AF_INET, socket.SOCK_STREAM,
                                       flags=socket.AI_NUMERICHOST)
        return dict(printer, host=addresses[0][4][0])
    except PermissionError:
        return dict(printer, connection_error="Allow LAN access in Orca, then reopen the viewer")
    except OSError:
        return dict(printer, connection_error="Check the printer address in Camera Printer Setup")


def viewer_settings(config):
    if not isinstance(config, dict):
        config = {}
    if config.get("demo") is True:
        return [], ""
    roster, error = printer_roster()
    if error:
        return [], error
    if not roster:
        return [], "Open Camera Printer Setup to add a printer, then run LAN cameras again."
    hidden = config.get("hidden", [])
    if not isinstance(hidden, list):
        hidden = []
    printers = [resolve_printer(p) for p in roster if camera_id(p) not in hidden]
    return printers, "" if printers else "Select at least one printer in Plugins → Config."


class Viewer:
    def __init__(self, printers, ca, error):
        self.printers = printers
        self.ca = ca
        self.error = error
        self.page = token_hex(16)
        self.epoch = -1
        self.generation = 0
        self.session = None
        self.deadline = 0
        self.closed = False
        self.changed = threading.Condition()
        self.worker = None
        self.window = None

    def open(self):
        html = (ASSETS / "page.html").read_text(encoding="utf-8").replace("__PAGE_ID__", self.page)
        self.window = orca.host.ui.create_window(title="LAN cameras", html=html, width=1100, height=520,
                                                 on_message=self.on_message, on_close=self.close)
        if self.printers:
            # Start from the audited Script invocation. UI callbacks only renew the lease and read snapshots.
            self.worker = threading.Thread(target=self._watch, name="P1S viewer", daemon=True)
            try:
                self.worker.start()
            except (OSError, RuntimeError):
                self.error = "Unable to start camera workers. Check the plugin’s threading permission."
                self.worker = None

    def close(self):
        with self.changed:
            self.closed = True
            if self.session:
                self.session.close()
            self.changed.notify()

    def _watch(self):
        try:
            while self.window.is_open():
                with self.changed:
                    if self.closed:
                        break
                    wanted = self.deadline > time.monotonic()
                    retiring = None
                    if self.session and (not wanted or not self.session.active()):
                        retiring, self.session = self.session, None
                        retiring.close()
                    elif wanted and self.session is None and not self.error:
                        try:
                            self.session = Session(self.printers, self.ca)
                            self.generation += 1
                        except (OSError, RuntimeError):
                            self.error = "Unable to start camera workers. Check the plugin’s threading permission."
                    if retiring is None:
                        self.changed.wait(.25)
                if retiring:
                    retiring.join()
        finally:
            self.close()
            if self.session:
                self.session.join()
            self.session = None
            self.printers = []

    def on_message(self, msg):
        if not isinstance(msg, dict) or msg.get("page") != self.page:
            return
        action, epoch = msg.get("action"), msg.get("epoch")
        if action not in ("poll", "hide") or type(epoch) is not int:
            return
        with self.changed:
            if self.closed or epoch < self.epoch:
                return
            if epoch != self.epoch and self.session:
                self.session.close()
            self.epoch = epoch
            if action == "hide":
                self.deadline = 0
                if self.session:
                    self.session.close()
                self.changed.notify()
                return
            self.deadline = time.monotonic() + LEASE_SECONDS
            if self.error:
                tiles = [dict(name="LAN cameras", state="Setup required", info=self.error)]
            elif not self.printers:
                step = int(time.monotonic() * 2)
                tiles = [dict(name=f"P1S {i+1}", state="Demo", info="Synthetic camera feed", demo=step) for i in range(2)]
            elif self.session and self.session.renew():
                seen = msg.get("seen", []) if msg.get("generation") == self.generation else []
                tiles = [tile.snapshot(seen[i] if isinstance(seen, list) and i < len(seen) else 0)
                         for i, tile in enumerate(self.session.tiles)]
            else:
                tiles = [dict(name=p["name"], state="Connecting", info="") for p in self.printers]
            generation = self.generation
            self.changed.notify()
        if self.window:
            self.window.post(dict(page=self.page, epoch=epoch, generation=generation,
                                  request=msg.get("request"), tiles=tiles))


class Cameras(orca.script.ScriptPluginCapabilityBase):
    def get_name(self):
        return "LAN cameras"

    def has_config_ui(self):
        return True

    def get_default_config(self):
        return {"hidden": [], "demo": False}

    def get_config_ui(self):
        printers, error = printer_roster()
        rows = "".join(
            f'<label><input type="checkbox" data-camera="{camera_id(p)}">{escape(p["name"])}</label>'
            for p in printers
        )
        if not rows:
            rows = f'<p>{escape(error or "No LAN printers have been added yet.")}</p>'
        return (ASSETS / "config.html").read_text(encoding="utf-8").replace("__CAMERA_ROWS__", rows)

    def on_load(self):
        self.lock = threading.Lock()
        self.viewers = []
        self.unloading = False

    def execute(self):
        with self.lock:
            if self.unloading:
                return orca.ExecutionResult.success("Camera viewer is closing.")
            self.viewers = [v for v in self.viewers if (v.window and v.window.is_open())
                            or (v.worker and v.worker.is_alive())]
            if any(not v.closed and v.window.is_open() for v in self.viewers):
                return orca.ExecutionResult.success("Camera viewer is already open.")
            printers, error = viewer_settings(json.loads(self.get_config()))
            viewer = Viewer(printers, (ASSETS / "printer-roots.pem").read_text(encoding="utf-8"), error)
            self.viewers.append(viewer)
            viewer.open()
        return orca.ExecutionResult.success("Camera viewer opened.")

    def on_unload(self):
        with self.lock:
            self.unloading = True
            viewers, self.viewers = self.viewers, []
        for viewer in viewers:
            viewer.close()
        for viewer in viewers:
            if viewer.worker and viewer.worker.ident is not None:
                viewer.worker.join()


@orca.plugin
class CameraPlugin(orca.base):
    def register_capabilities(self):
        orca.request_permissions(fs_read=[str(PRINTER_FILE)], threading=True)
        orca.register_capability(Cameras)
