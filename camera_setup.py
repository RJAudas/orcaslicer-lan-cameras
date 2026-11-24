"""User-run local setup for LAN cameras. This program ships separately from the plugin."""
from contextlib import contextmanager
from getpass import GetPassWarning, getpass
import json
import os
from pathlib import Path
import sys
import tempfile
import warnings

from p1s_cameras.settings import PRINTER_FILE, read_printers, validate


def default_profile():
    if sys.platform == "win32":
        parent = Path(os.environ["APPDATA"])
    elif sys.platform == "darwin":
        parent = Path.home() / "Library/Application Support"
    else:
        parent = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return parent / "OrcaSlicer" / "OrcaSlicer.conf"


def paired_printers(path):
    # AppConfig can append a checksum comment after the JSON object.
    config = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8-sig").lstrip())[0]
    if not isinstance(config, dict) or not isinstance(config.get("local_machines", {}), dict):
        raise ValueError("Choose an OrcaSlicer profile with saved LAN printers.")
    rows = []
    for serial, device in config.get("local_machines", {}).items():
        if not isinstance(device, dict) or device.get("printer_type") not in ("C11", "C12"):
            continue
        code = device.get("access_code")
        for key in ("access_code", "user_access_code"):
            codes = config.get(key)
            if not code and isinstance(codes, dict):
                code = codes.get(serial)
        row = dict(name=device.get("dev_name") or "P1 printer", host=device.get("dev_ip"), serial=serial, code=code)
        try:
            validate([row])
        except ValueError:
            continue
        rows.append(row)
    return rows


def merge_printers(current, additions):
    by_serial = {p["serial"].upper(): p for p in current}
    for printer in additions:
        by_serial[printer["serial"].upper()] = printer
    merged = list(by_serial.values())
    validate(merged)
    return merged


@contextmanager
def settings_lock(path):
    # Keep a stable lock file when replacing the settings inode. Closing the handle
    # releases the OS lock, including when a setup process exits unexpectedly.
    with path.with_suffix(".lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another setup window is saving your printers. Try again.") from None
        yield


def save_printers(path, original, rows):
    validate(rows)
    with settings_lock(path):
        if read_printers(path) != original:
            raise ValueError("Another setup window changed your printers. Reopen setup before saving.")
        temp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
        try:
            with temp:
                json.dump(rows, temp, ensure_ascii=False)
            os.replace(temp.name, path)
        finally:
            Path(temp.name).unlink(missing_ok=True)


def graphical_setup(root, tk, original):
    from tkinter import filedialog, messagebox, ttk

    rows = list(original)
    root.title("Camera Printer Setup")
    body = ttk.Frame(root, padding=14)
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="LAN printers").pack(anchor="w")
    roster = tk.Listbox(body, width=54, height=8, selectmode="extended", exportselection=False)
    roster.pack(fill="both", expand=True, pady=6)

    def refresh():
        roster.delete(0, "end")
        for p in rows:
            roster.insert("end", f'{p["name"]}   ({p["host"]})')

    def dialog(title):
        window = tk.Toplevel(root)
        window.title(title)
        window.transient(root)
        window.grab_set()
        panel = ttk.Frame(window, padding=14)
        panel.pack(fill="both", expand=True)
        return window, panel

    def merge(window, additions):
        try:
            rows[:] = merge_printers(rows, additions)
        except ValueError as error:
            messagebox.showerror("Printer settings", str(error), parent=window)
            return
        refresh()
        window.destroy()

    def choose_profile():
        profile = default_profile()
        selected = filedialog.askopenfilename(parent=root, title="Choose the OrcaSlicer profile to import",
                                              initialdir=profile.parent, initialfile=profile.name)
        if selected:
            import_printers(Path(selected))

    def import_printers(profile=None):
        if profile is None:
            profile = default_profile()
            if not profile.is_file():
                choose_profile()
                return
        empty_message = "No saved P1 LAN pairings with an address and access code were found."
        try:
            candidates = paired_printers(profile)
        except (ValueError, OSError):
            candidates = []
            empty_message = "Could not read the saved OrcaSlicer profile."
        window, panel = dialog("Import saved LAN printers")
        ttk.Label(panel, text=str(profile), wraplength=480).pack(anchor="w")

        def change_profile():
            window.destroy()
            choose_profile()

        ttk.Button(panel, text="Choose another profile…", command=change_profile).pack(anchor="w", pady=6)
        if not candidates:
            ttk.Label(panel, text=empty_message).pack()
            return
        ttk.Label(panel, text="Choose which pairings to use, including their LAN access codes.").pack(anchor="w")
        choices = tk.Listbox(panel, selectmode="multiple", exportselection=False,
                             width=54, height=min(len(candidates), 12))
        choices.pack(fill="both", expand=True, pady=6)
        for p in candidates:
            choices.insert("end", f'{p["name"]}   ({p["host"]})')
        ttk.Button(panel, text="Add selected", command=lambda: merge(
            window, [candidates[i] for i in choices.curselection()])).pack(anchor="e")

    def add_manually():
        window, panel = dialog("Add or update a printer")
        entries = {}
        for key, label in (("name", "Printer name"), ("host", "LAN IPv4 address"),
                           ("serial", "Printer serial number"), ("code", "LAN access code")):
            ttk.Label(panel, text=label).pack(anchor="w")
            entry = ttk.Entry(panel, width=38, show="*" if key == "code" else "")
            entry.pack(fill="x", pady=(0, 6))
            entries[key] = entry
        entries["name"].focus_set()
        ttk.Button(panel, text="Add printer", command=lambda: merge(
            window, [{k: v.get().strip() for k, v in entries.items()}])).pack(anchor="e")

    def remove():
        for index in reversed(roster.curselection()):
            rows.pop(index)
        refresh()

    def save():
        try:
            save_printers(PRINTER_FILE, original, rows)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not save printers", str(error), parent=root)
            return
        messagebox.showinfo("Printers saved", "In OrcaSlicer, reopen Plugins → Config → LAN cameras.", parent=root)
        root.destroy()

    buttons = ttk.Frame(body)
    buttons.pack(fill="x")
    for label, action in (("Import from Device…", import_printers), ("Add manually…", add_manually), ("Remove", remove)):
        ttk.Button(buttons, text=label, command=action).pack(side="left", padx=(0, 6))
    ttk.Label(body, wraplength=440, text="Save stores the listed pairings and their LAN access codes in a plaintext file "
              "in your user profile. OrcaSlicer’s Device list is read-only here.").pack(anchor="w", pady=12)
    ttk.Button(body, text="Save", command=save).pack(anchor="e")
    refresh()
    root.mainloop()


def numbers(rows):
    for i, p in enumerate(rows, 1):
        print(f'{i}. {p["name"]} ({p["host"]})')
    choice = input("Select numbers separated by commas, 'all', or Enter to cancel: ").strip()
    if choice.lower() == "all":
        return list(rows)
    if not choice:
        return []
    indices = [int(n.strip()) for n in choice.split(",")]
    if not all(1 <= i <= len(rows) for i in indices):
        raise ValueError("Select a number from the list.")
    return [rows[i-1] for i in dict.fromkeys(indices)]


def console_setup(original):
    rows = list(original)
    print("Camera Printer Setup")
    while True:
        print("\nCurrent printers:", ", ".join(p["name"] for p in rows) or "none")
        action = input("[I]mport from Device, [A]dd manually, [R]emove, [S]ave or [Q]uit: ").strip().lower()
        try:
            if action == "i":
                profile = default_profile()
                path = input(f"Orca profile to read [{profile}]: ").strip()
                candidates = paired_printers(Path(path) if path else profile)
                if candidates:
                    print("Choose saved LAN pairings to import, including their access codes.")
                    rows = merge_printers(rows, numbers(candidates))
                else:
                    print("No saved P1 LAN pairings with an address and access code were found.")
            elif action == "a":
                printer = dict(name=input("Printer name: ").strip(), host=input("LAN IPv4 address: ").strip(),
                               serial=input("Printer serial: ").strip())
                with warnings.catch_warnings():
                    warnings.simplefilter("error", GetPassWarning)
                    printer["code"] = getpass("LAN access code: ")
                rows = merge_printers(rows, [printer])
            elif action == "r":
                removed = numbers(rows)
                rows = [p for p in rows if p not in removed]
            elif action == "s":
                if input("Save these printers and their codes as plaintext in your user profile? [y/N] ").lower() == "y":
                    save_printers(PRINTER_FILE, original, rows)
                    print("Saved. Reopen Config in OrcaSlicer.")
                    return
            elif action == "q":
                return
        except (ValueError, OSError) as error:
            print(f"Setup could not complete that action: {error}")
        except GetPassWarning:
            print("Enter access codes in a terminal that supports private input.")


def main():
    try:
        original = read_printers(PRINTER_FILE)
    except (ValueError, OSError):
        raise SystemExit(f"Cannot read existing settings at {PRINTER_FILE}. Check the file before running setup again.") from None
    if "--console" not in sys.argv:
        try:
            import tkinter as tk
        except ImportError:
            pass
        else:
            try:
                root = tk.Tk()
            except tk.TclError:
                pass
            else:
                graphical_setup(root, tk, original)
                return
    try:
        console_setup(original)
    except (EOFError, KeyboardInterrupt):
        print("Setup closed.")


if __name__ == "__main__":
    main()
