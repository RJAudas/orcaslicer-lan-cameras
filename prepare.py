"""Build the redistributable wheel and the separate end-user setup download."""
import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent
VERSION = "0.2.0"
WHEEL = f"p1s_cameras-{VERSION}-py3-none-any.whl"


def build(output):
    package = ROOT / "p1s_cameras"
    files = {f"p1s_cameras/{name}": (package / name).read_bytes()
             for name in ("monitor.py", "settings.py", "transport.py", "page.html", "config.html", "printer-roots.pem")}
    files["p1s_cameras/__init__.py"] = (package / "plugin.py").read_bytes()
    dist = f"p1s_cameras-{VERSION}.dist-info"
    files[f"{dist}/licenses/LICENSE"] = (ROOT / "LICENSE").read_bytes()
    files[f"{dist}/METADATA"] = (f"Metadata-Version: 2.4\nName: p1s_cameras\nVersion: {VERSION}\n"
                                  "Summary: Live LAN views from Bambu Lab P1 printers\n"
                                  "Author: RJAudas\nLicense-Expression: AGPL-3.0-only\nLicense-File: LICENSE\nRequires-Python: >=3.12\n").encode("ascii")
    files[f"{dist}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: prepare.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        writer.writerow([name, "sha256=" + digest, len(data)])
    writer.writerow([f"{dist}/RECORD", "", ""])
    files[f"{dist}/RECORD"] = record.getvalue().encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output / WHEEL, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return output / WHEEL


def build_download(wheel):
    setup = io.BytesIO()
    with zipfile.ZipFile(setup, "w", zipfile.ZIP_DEFLATED) as app:
        app.write(ROOT / "camera_setup.py", "__main__.py")
        app.writestr("p1s_cameras/__init__.py", "")
        app.write(ROOT / "p1s_cameras/settings.py", "p1s_cameras/settings.py")
    download = wheel.parent / f"p1s_cameras-{VERSION}.zip"
    with zipfile.ZipFile(download, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(wheel, wheel.name)
        launcher = (ROOT / "Camera Printer Setup.cmd").read_text(encoding="ascii")
        archive.writestr("Camera Printer Setup.cmd", launcher.replace("\n", "\r\n").encode("ascii"))
        archive.writestr("Camera Printer Setup.pyz", setup.getvalue())
        archive.write(ROOT / "README.md", "README.md")
        archive.write(ROOT / "LICENSE", "LICENSE")
    return download


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path.cwd() / "dist")
    args = parser.parse_args()
    wheel = build(args.output)
    print("Plugin:", wheel.resolve())
    print("End-user download:", build_download(wheel).resolve())
