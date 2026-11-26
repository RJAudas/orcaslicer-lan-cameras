import base64
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import queue
import struct
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
import zipfile

from p1s_cameras import monitor, transport
from p1s_cameras.settings import camera_id
from prepare import build


class ChunkedStream:
    def __init__(self, data):
        self.data = data
        self.written = b""

    def recv(self, size):
        size = min(3, size)
        result, self.data = self.data[:size], self.data[size:]
        return result

    def sendall(self, data):
        self.written += data


class CameraTests(unittest.TestCase):
    def test_camera_reads_fragmented_headers_and_adjacent_frames(self):
        frames = [b"\xff\xd8\xffone\xff\xd9", b"\xff\xd8\xfftwo\xff\xd9"]
        stream = ChunkedStream(b"".join(struct.pack("<4I",len(f),0,1,0)+f for f in frames))
        results = []
        with patch.object(transport, "connection", return_value=nullcontext(stream)):
            transport.camera({"code":"fake"}, "", lambda: len(results) < 2, results.append)
        self.assertEqual([base64.b64decode(v) for v in results], frames)
        self.assertEqual(len(stream.written), 80)

    def test_invalid_length_does_not_replace_last_frame(self):
        stream = ChunkedStream(struct.pack("<4I",2**32-1,0,1,0))
        tile = monitor.Tile("Left")
        tile.frame("previous")
        with patch.object(transport, "connection", return_value=nullcontext(stream)), self.assertRaises(ValueError):
            transport.camera({"code":"fake"}, "", lambda: True, tile.frame)
        self.assertEqual(tile.snapshot(0)["jpeg"], "previous")
        self.assertNotIn("jpeg", tile.snapshot(1))

    def test_reports_merge_per_job_and_expire(self):
        t = monitor.Tile("Left")
        t.report(dict(subtask_name="Gear", gcode_state="RUNNING", mc_percent="0", mc_remaining_time=9))
        t.report(dict(mc_percent=1))
        self.assertEqual(t.snapshot(0)["info"], "Gear · RUNNING · 1% · 9 min remaining")
        t.report(dict(subtask_name="Case", mc_percent="bad"))
        self.assertEqual(t.snapshot(0)["info"], "Case")
        with patch("time.monotonic", return_value=t.last_report + 31):
            self.assertEqual(t.snapshot(0)["info"], "Print status unavailable")

    def test_remaining_time_displays_hours_and_minutes(self):
        for minutes, expected in (
            (0, "0 min remaining"),
            (59, "59 min remaining"),
            (60, "1 h 0 min remaining"),
            ("991", "16 h 31 min remaining"),
            (1505, "25 h 5 min remaining"),
        ):
            with self.subTest(minutes=minutes):
                self.assertEqual(monitor.job_summary({"mc_remaining_time": minutes}), expected)

    def test_status_reader_accepts_only_the_configured_report_topic(self):
        fields = {"command": "push_status", "gcode_state": "RUNNING", "mc_percent": "13"}
        payload = json.dumps({"print": fields}).encode("utf-8")
        stream = ChunkedStream(
            transport.packet(0x20, b"\x00\x00") + transport.packet(0x90, b"\x00\x01\x00")
            + transport.packet(0x30, transport.mqtt_string("device/OTHER/report") + payload)
            + transport.packet(0x30, transport.mqtt_string("device/PRINTER/report") + payload)
        )
        reports = []
        with patch.object(transport, "connection", return_value=nullcontext(stream)):
            transport.status({"serial": "PRINTER", "code": "fake"}, "", lambda: not reports, reports.append)
        self.assertEqual(reports, [fields])
        self.assertIn(b"device/PRINTER/report", stream.written)
        self.assertIn(b'"command":"pushall"', stream.written)

    def test_one_failed_reader_reconnects_while_its_peer_updates(self):
        events = [queue.Queue(), queue.Queue()]
        def camera(printer, ca, active, update):
            while active():
                try:
                    item = events[printer["index"]].get(timeout=.01)
                except queue.Empty:
                    continue
                if item is None:
                    raise EOFError
                update(item)
        def idle(printer, ca, active, update):
            while active():
                time.sleep(.01)
        session = monitor.Session([dict(name="Left",index=0), dict(name="Right",index=1)], "", camera, idle)
        self.addCleanup(session.join)
        self.addCleanup(session.close)
        def wait_for(predicate):
            deadline = time.monotonic() + 3
            while not predicate():
                self.assertLess(time.monotonic(), deadline)
                self.assertTrue(session.renew())
                time.sleep(.01)
        events[0].put("a")
        events[1].put("b")
        a, b = session.tiles
        wait_for(lambda: a.revision == b.revision == 1)
        events[0].put(None)
        wait_for(lambda: a.state == "Retrying")
        events[1].put("c")
        wait_for(lambda: b.revision == 2)
        self.assertEqual(a.snapshot(0)["jpeg"], "a")
        events[0].put("d")
        wait_for(lambda: a.state == "Live" and a.revision == 2)
        session.deadline = time.monotonic()-1
        self.assertFalse(session.renew())
        for worker in session.threads:
            worker.join(1)
            self.assertFalse(worker.is_alive())

    def test_packaged_page_lifecycle_excludes_credentials_and_old_sessions(self):
        printers = [dict(name="Left <&>",host="192.0.2.1",serial="FAKESERIAL01",code="test-credential")]
        windows, grants, registered = [], [], []

        class Window:
            def __init__(self, **kwargs):
                self.options = kwargs
                self.open = True
                self.replies = []
                windows.append(self)

            def is_open(self):
                return self.open

            def post(self, data):
                self.replies.append(data)

        orca = types.SimpleNamespace(
            script=types.SimpleNamespace(ScriptPluginCapabilityBase=object),
            base=object, plugin=lambda cls: cls, register_capability=registered.append,
            request_permissions=lambda **kw: grants.append(kw),
            host=types.SimpleNamespace(ui=types.SimpleNamespace(create_window=Window)),
            ExecutionResult=types.SimpleNamespace(success=str),
        )

        def wait_for(predicate):
            limit = time.monotonic() + 3
            while not predicate():
                self.assertLess(time.monotonic(), limit)
                time.sleep(.01)

        def idle(printer, ca, active, update):
            update("test-frame")
            while active():
                time.sleep(.01)

        def quiet(printer, ca, active, update):
            while active():
                time.sleep(.01)

        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {"orca":orca}):
            path = Path(directory)
            wheel = build(path)
            with zipfile.ZipFile(wheel) as archive:
                for file in archive.namelist():
                    self.assertNotIn(b"test-credential", archive.read(file))
                    self.assertNotIn(b"192.0.2.1", archive.read(file))
                self.assertNotIn("camera_setup.py", archive.namelist())
                archive.extractall(path)
            spec = importlib.util.spec_from_file_location("installed_cameras", path/"p1s_cameras/__init__.py")
            pkg = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = pkg
            spec.loader.exec_module(pkg)
            pkg.PRINTER_FILE = path/"setup.json"
            pkg.PRINTER_FILE.write_text(json.dumps(printers))
            pkg.CameraPlugin().register_capabilities()
            self.assertEqual(grants, [dict(fs_read=[str(pkg.PRINTER_FILE)], threading=True)])
            cap = registered[0]()
            cap.on_load()
            self.assertTrue(cap.has_config_ui())
            config_html = cap.get_config_ui()
            self.assertIn("Left &lt;&amp;&gt;", config_html)
            self.assertIn(camera_id(printers[0]), config_html)
            for private in ("test-credential", "192.0.2.1", "FAKESERIAL01"):
                self.assertNotIn(private, config_html)
            with patch.object(pkg.socket, "getaddrinfo", side_effect=PermissionError) as resolve:
                selected, error = pkg.viewer_settings({"hidden": [camera_id(printers[0])]})
                self.assertEqual(selected, [])
                self.assertIn("Select", error)
                self.assertEqual(pkg.viewer_settings({"demo": True}), ([], ""))
                resolve.assert_not_called()
                selected, error = pkg.viewer_settings({})
                self.assertFalse(error)
                blocked = monitor.Session(selected, "")
                self.assertEqual(blocked.threads, [])
                self.assertIn("Allow LAN access", blocked.tiles[0].state)
            cap.get_config = lambda: "{}"
            with patch.object(pkg, "Session", side_effect=lambda p, ca: monitor.Session(p, ca, idle, quiet)):
                self.addCleanup(cap.on_unload)
                cap.execute()
                cap.execute()
                self.assertEqual(len(windows), 1)
                first = cap.viewers[0]
                def send(viewer, action, epoch):
                    viewer.on_message(dict(page=viewer.page, request=1, action=action, epoch=epoch, seen=[]))
                send(first, "poll", 1)
                wait_for(lambda: first.session and first.session.tiles[0].revision)
                previous = first.session
                send(first, "hide", 2)
                send(first, "poll", 1)
                wait_for(lambda: first.session is None)
                send(first, "poll", 3)
                wait_for(lambda: first.session and first.session.tiles[0].revision)
                previous.tiles[0].frame("late frame")
                send(first, "poll", 3)
                self.assertNotIn("late frame", str(windows[0].replies[-1]))
                for private in ("test-credential", "192.0.2.1", "FAKESERIAL01"):
                    self.assertNotIn(private, windows[0].options["html"] + json.dumps(windows[0].replies))
                # Force-destroy the underlying handle without a JS or user close callback.
                windows[0].open = False
                first.worker.join(2)
                self.assertFalse(first.worker.is_alive())
                cap.execute()
                second = cap.viewers[-1]
                send(second, "poll", 1)
                wait_for(lambda: second.session and second.session.tiles[0].revision)
                last_session = second.session
                old_messages = len(first.window.replies)
                send(first, "poll", 9)
                self.assertEqual(len(first.window.replies), old_messages)
                cap.on_unload()
                self.assertFalse(second.worker.is_alive())
                self.assertFalse(any(t.is_alive() for t in last_session.threads))
