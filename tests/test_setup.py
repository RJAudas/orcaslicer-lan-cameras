import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from camera_setup import merge_printers, paired_printers, save_printers, settings_lock
from p1s_cameras.settings import camera_id, read_printers
from prepare import build, build_download


def printer(index):
    return dict(name=f"Printer {index}", host=f"192.0.2.{index}", serial=f"TESTSERIAL{index}", code=f"fake-code-{index}")


class SetupTests(unittest.TestCase):
    def test_import_reads_only_complete_p1_pairings_and_preserves_other_choices(self):
        rows = [printer(i) for i in range(1, 5)]
        config = {"local_machines": {}, "access_code": {}, "user_access_code": {},
                  "app": {"last_selected_machine": "untouched"}}
        for i, row in enumerate(rows[:3]):
            saved = dict(dev_name=row["name"], dev_ip=row["host"], printer_type=("C12", "C11", "C12")[i])
            config["local_machines"][row["serial"]] = saved
            if i == 0:
                saved["access_code"] = row["code"]
            else:
                config[("access_code", "user_access_code")[i-1]][row["serial"]] = row["code"]
        config["local_machines"]["NONP1SERIAL"] = dict(printer_type="X1", dev_ip="192.0.2.7", access_code="fake")
        config["local_machines"]["INCOMPLETE"] = dict(printer_type="C12", dev_ip="192.0.2.8")
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "OrcaSlicer.conf"
            content = json.dumps(config) + "\n# MD5 checksum fixture"
            profile.write_text(content, encoding="utf-8-sig")
            candidates = paired_printers(profile)
            self.assertEqual(candidates, rows[:3])
            # Only the selected pairing is added; an untouched fourth printer is retained.
            chosen = merge_printers([rows[3]], [candidates[1]])
            self.assertEqual(chosen, [rows[3], rows[1]])
            self.assertEqual(profile.read_text(encoding="utf-8-sig"), content)

    def test_existing_roster_supports_more_than_two_and_changed_ips_keep_the_same_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "printers.json"
            legacy = [printer(1), printer(2)]
            file.write_text(json.dumps(legacy))
            self.assertEqual(read_printers(file), legacy)
            moved = dict(legacy[0], host="192.0.2.99", name="New name", code="new-fake")
            rows = merge_printers(legacy, [moved, printer(3)])
            self.assertEqual(camera_id(moved), camera_id(legacy[0]))
            with settings_lock(file):
                with self.assertRaisesRegex(ValueError, "saving"):
                    save_printers(file, legacy, rows)
            save_printers(file, legacy, rows)
            self.assertEqual(read_printers(file), [moved, legacy[1], printer(3)])
            with self.assertRaises(ValueError):
                save_printers(file, legacy, [])
            with self.assertRaises(ValueError):
                save_printers(file, rows, rows + [dict(printer(4), host=moved["host"])])
            self.assertEqual(read_printers(file), rows)
            save_printers(file, rows, [])
            self.assertEqual(read_printers(file), [])

    def test_end_user_setup_runs_outside_orca_from_the_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = build(Path(tmp))
            with zipfile.ZipFile(build_download(wheel)) as download:
                self.assertIn("Camera Printer Setup.cmd", download.namelist())
                download.extract("Camera Printer Setup.pyz", tmp)
            profile = Path(tmp) / "OrcaSlicer.conf"
            rows = [printer(1), printer(2)]
            config = {"local_machines": {
                p["serial"]: dict(dev_name=p["name"], dev_ip=p["host"], access_code=p["code"], printer_type="C12")
                for p in rows
            }}
            profile.write_text(json.dumps(config))
            result = subprocess.run([sys.executable, str(Path(tmp) / "Camera Printer Setup.pyz"), "--console"],
                                    input=f"i\n{profile}\n2\ns\ny\n", capture_output=True, text=True,
                                    env=dict(os.environ, HOME=tmp, USERPROFILE=tmp))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(read_printers(Path(tmp) / ".orca-p1s-cameras.json"), [rows[1]])
            for p in rows:
                self.assertNotIn(p["code"], result.stdout + result.stderr)
