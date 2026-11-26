# P1 LAN cameras for OrcaSlicer

View live cameras from Bambu Lab P1S and P1P printers in a resizable window beside Device. Each tile shows the printer’s name, camera state and available job name, print state, progress and remaining time. The viewer uses two columns and switches to one below 690 CSS pixels.

## Install

Use an OrcaSlicer build with **Plugins → Install local plugin** and support for Script plugins. Download the setup bundle from the [latest release](https://github.com/RJAudas/orcaslicer-lan-cameras/releases/latest).

1. Extract the downloaded ZIP. In OrcaSlicer, open **Plugins → Install local plugin**, choose the included `p1s_cameras` wheel, and activate it. The same wheel works for every printer list.
2. Run **Camera Printer Setup** once to add printers. Windows users can double-click `Camera Printer Setup.cmd`. On macOS and Linux, run `python3 "Camera Printer Setup.pyz"` in the extracted folder. The graphical assistant uses Python with Tk. Without Tk, the assistant offers the same import and manual-entry choices in the terminal. The Windows launcher can use the Python runtime shipped with OrcaSlicer in its default installation.
3. Choose **Import from Device…**, select the saved P1 LAN printers you want by name, and click **Add selected → Save**. Setup finds the standard OrcaSlicer profile automatically. Use **Choose another profile…** for a portable installation. **Add manually…** accepts a display name, LAN IPv4 address, serial number and access code.
4. In Orca, select **p1s_cameras → Config → LAN cameras**. Check the printers to show and click **Save**. Expand the plugin row and click **▶ Run** beside **LAN cameras**. Orca may ask to allow LAN access the first time you use each address. If a tile asks for permission, approve the prompt, then hide and reopen the viewer.

**Upgrading a local install:** Hide the viewer and deactivate the older plugin before activating the new version. Your existing camera settings are read automatically. Each version registers a script named **LAN cameras**, and Orca identifies local wheels by filename.

## Manage cameras

Run Camera Printer Setup again to import more printers, update a pairing or remove a printer from the plugin. Import matches by serial number, so a changed name, IP or access code updates the existing entry. Unselected pairings are left in place. **Save** commits your changes. Reopen the Config tab to refresh its list and reopen the camera viewer to apply your selection.

Config can also preview two synthetic cameras. **Restore defaults** selects all configured printers and returns to live mode. Closing the viewer or clicking **Hide cameras** stops its readers.

## Pairings and permissions

The setup assistant runs separately and reads the Orca profile only when you select it for import. It copies the chosen pairings into `.orca-p1s-cameras.json` in your home directory. This plaintext file contains display names, addresses, serials and LAN access codes. On Windows it inherits your profile’s ACL. On POSIX it is created with owner-only permissions. To delete all saved camera credentials, delete that file. Removing entries here affects only the camera plugin.

The wheel contains the plugin and public printer CA roots. On activation it requests permission to read the camera settings file and start threads. Connections use Orca’s per-host socket grants at runtime. The Config page receives display names and opaque selection IDs. Access codes stay out of wheels, HTML, JavaScript, Orca capability configs, logs and command lines.

The plugin API at the development baseline (`e23af6d7b0`) exposes limited metadata for the currently selected device. It does not expose the paired Device list or a credential-aware native camera handle. The assistant imports saved LAN pairings as a snapshot. A live **Choose from Device** picker inside Orca would require a host API.

## Playback

The camera and MQTT report readers connect independently to ports 6000 and 8883. Both verify the printer certificate against Orca’s printer roots and the expected serial before sending the LAN login. The MQTT reader subscribes to the printer’s report topic and requests a full status snapshot. The viewer exposes no printer controls.

The viewer pulls the latest frame, retains the previous picture through a retry, and expires print status after 30 seconds without a report. A polling lease and the native window handle cover lost updates and forced destruction. Unload signals and joins outstanding workers. A modeless Script window provides a reliable close lifecycle on Windows, where a hidden notebook page can continue polling.

## Development and verification

From the repository root, run `python prepare.py` to build the wheel and end-user ZIP in `dist/`. Building requires no printer settings. Run `python -m unittest discover -s tests -v` for the offline suite, including selection, private data separation, profile import, upgrade compatibility, packaged setup, camera retry, telemetry, and viewer teardown.

The original viewer was tested against two paired LAN printers on Windows. Both delivered 1280 × 720 frames and status reports. Disconnecting the first reader retained its last image while the other advanced; it reconnected independently. The wxWidgets/WebView2 harness also checked narrow layout, reopen, forced closure and worker cleanup. macOS and Linux have not been exercised on hardware.

## Publishing a release

Update `VERSION` in `prepare.py` and publish a GitHub release from that commit with a matching tag, such as `v0.2.1`. Use a version higher than the current Orca Cloud listing. The release workflow tests the tagged source, builds the wheel and publishes it to P1 LAN Cameras with the GitHub release notes as its changelog. GitHub supplies a short-lived OIDC credential. For the standalone downloads, attach the wheel and setup ZIP produced by `python prepare.py` to the GitHub release.

The plugin owner connects `RJAudas/orcaslicer-lan-cameras` once under **Edit plugin → GitHub publishing**. [Orca Cloud publishing guide](https://cloud.orcaslicer.com/wiki/#publish-from-github)

## License and credits

This community plugin uses the GNU Affero General Public License, version 3. The bundled printer CA certificates come from OrcaSlicer. [License](LICENSE) · [Upstream certificate bundle](https://github.com/OrcaSlicer/OrcaSlicer/blob/e23af6d7b075ab6e317485551c7cbee33d1b4c4b/resources/cert/printer.cer)
