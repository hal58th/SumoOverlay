# Sumo Broadcast Overlay

This program downloads a basho's bout lists from [sumo-api.com](https://sumo-api.com), lets you move between bouts with a control page, and displays two OBS browser overlays:

1. **Main overlay** — a compact, persistent strip in the top-left with the current division, wrestler names, their records before this bout, and abbreviated ranks.
2. **Pre-match overlay** — a larger matchup card with ranks, pre-bout records, and head-to-head (H2H) wins. When enabled, it appears after a bout is selected and hides after the configured number of seconds. This timer works the same in manual and automated modes; set the duration to `0` or use the Control page toggle to turn it off.

Settings are read from `config.json`; the program does not use environment variables. The app watches that file and reloads valid changes while it is running. OCR/screen capture and automatic match detection are not implemented yet. “Automated” is reserved for future OCR-driven bout advancement; until then, bout selection uses the Previous/Next buttons in either mode.

## Requirements

- Python 3.10 or newer.
- An internet connection to install packages and fetch data from sumo-api.com.
- OBS Studio, if you want to stream the overlays.
- This project folder. The examples assume it is `C:\SumoOverlay` in Windows, or `/mnt/c/SumoOverlay` from WSL.

The app uses CPU-only Python packages; EasyOCR, PyTorch, and a GPU are not required.

## Configure the app

Open `config.json` in a text editor and change the values you need. Keep the braces, commas, quotation marks, and key names as shown: the file must remain valid JSON. The included values are an example basho; change `basho_id` and `day` to the tournament/day you want.

| Setting | Purpose |
|---|---|
| `basho_id` | Six-digit basho ID in `YYYYMM` format, such as `"202505"`. |
| `division` | `0` loads all divisions (recommended); `1` Makuuchi, `2` Juryo, `3` Makushita, `4` Sandanme, `5` Jonidan, `6` Jonokuchi. |
| `day` | Tournament day, from `1` through `15`. |
| `api.base_url` | sumo-api.com API root. |
| `api.timeout_seconds` | Maximum wait for an API request. |
| `server.host`, `server.port` | Local web-server address and port. Keep the host set to `127.0.0.1` unless you know why it needs to change. |
| `server.watch_interval_seconds` | How often, in seconds, the app checks `config.json` for edits. |
| `control.mode` | `"manual"` or `"automated"`. This selects how bouts will be controlled when OCR match detection is implemented; it does not change pre-match overlay behavior today. |
| `control.pre_match_seconds` | How long, in seconds, the pre-match overlay appears after a bout change. `0` disables timed auto-show. |
| `control.pre_match_pinned` | `true` or `false`. When true, keeps the pre-match overlay visible until you hide it from the Control page. |
| `overlay.canvas` | The OBS browser-source design width and height, in pixels. |
| `overlay.main` | Main overlay rectangle: top-left `x`, `y`, `width`, and `height`, in pixels on the canvas. It centers each wrestler's name over their rank and record (shown side by side); division and “VS” are in the middle. |
| `overlay.pre_match` | Pre-match overlay rectangle, using the same pixel coordinate system. The default covers almost the full canvas. |
| `capture_regions.abema_scoresheet` | Rectangle around the ABEMA scoresheet, in desktop screen pixels. Reserved for the future OCR reader; OCR does not currently use it. |

Both overlay rectangles must fit within `overlay.canvas`. Capture-region coordinates refer to the desktop/video capture area, not the OBS overlay canvas. If ABEMA moves its scoresheet, update the capture rectangle to match the new screen location. The main and pre-match overlay rectangles can be adjusted separately. The default pre-match rectangle is almost full screen; the card background is 50% opaque so the broadcast remains visible behind it while the text stays fully readable.

`division: 0` loads all divisions by requesting each of the six supported divisions separately; the API does not provide a working all-divisions endpoint. Bouts are combined in broadcast order. Values 1 through 6 select one division; number 1 is the top division (Makuuchi) and number 6 is Jonokuchi. For compatibility, existing configuration files using `"all"` or division names are still accepted; use the numeric values in new or edited settings.

When you save a valid JSON file, the app applies changes within the configured watch interval. A change to the basho, division, or day reloads its bout data and selects the first bout. Invalid JSON/settings or unavailable API data leave the last working settings active; the Control page reports a reload error. Fix the file and save again. Changing `server.host` or `server.port` requires restarting the app because the listening socket cannot be rebound while running.

## Install and run on Windows PowerShell

These instructions are recommended when OBS is running on Windows.

### 1. Open the project folder

Open PowerShell from the Windows Start menu and enter:

```powershell
cd C:\SumoOverlay
```

If the project is somewhere else, replace the path. `Get-ChildItem` should show `app.py`, `config.json`, and `pyproject.toml`.

### 2. Create a Python environment for this app

The Windows `python` command may be redirected to the Microsoft Store even when Python is installed. Use the Python Launcher, `py`, to find Python:

```powershell
py --version
py -0p
```

Choose an installed Python version that is 3.10 or newer. For example, if Python 3.11 is listed, run:

```powershell
py -3.11 -m venv .venv
```

Replace `3.11` with your installed version if it is different. You only need to create `.venv` once. If it already exists, skip this command.

### 3. Install the app

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

Use the `.venv` Python shown in these commands to install and run the app. It installs Flask and the other app packages just for this project.

### 4. Start the app

Make any changes to `config.json`, then run:

```powershell
.\.venv\Scripts\python.exe app.py
```

Keep the PowerShell window open while using the overlays. Press **Ctrl+C** in that window to stop the app.

If PowerShell says `python` is not installed, use `py` and `.\.venv\Scripts\python.exe` as shown above; the standalone `python` command is not needed. If `py` is also not recognized, turn off the `python.exe` and `python3.exe` App Installer aliases under **Settings → Apps → Advanced app settings → App execution aliases**, reopen PowerShell, and try again. If Python is not installed, install Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/), selecting **Add python.exe to PATH** and the Python Launcher if offered.

## Install and run on AlmaLinux 9 in WSL

WSL is a Linux environment that runs alongside Windows. It has its own Linux Python and packages, separate from Windows. For the simplest OBS setup, Windows PowerShell is recommended; use these instructions if you want the app to run inside WSL.

### 1. Open AlmaLinux and find the project

Open **AlmaLinux 9** from the Windows Start menu. If the project is in `C:\SumoOverlay`, its WSL path is `/mnt/c/SumoOverlay`:

```bash
cd /mnt/c/SumoOverlay
ls
```

The files should include `app.py`, `config.json`, and `pyproject.toml`. If the project is on another Windows drive, use `/mnt/<lowercase-drive-letter>/...`; for example `D:\Projects\SumoOverlay` is `/mnt/d/Projects/SumoOverlay`.

### 2. Install Python and make a WSL-only environment

```bash
sudo dnf install -y python3.11 python3.11-pip
mkdir -p "$HOME/.venvs"
python3.11 -m venv "$HOME/.venvs/sumo-overlay"
source "$HOME/.venvs/sumo-overlay/bin/activate"
```

If Linux asks for your password, type it and press **Enter**. It will not show characters as you type. When the environment is active, the command prompt usually begins with `(sumo-overlay)`.

### 3. Install and start the app

From the project folder, install the app and run it:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
python app.py
```

Keep this terminal open while using the overlays. Press **Ctrl+C** to stop the app. After opening a new AlmaLinux terminal, run `cd /mnt/c/SumoOverlay` and `source "$HOME/.venvs/sumo-overlay/bin/activate"` again before starting the app. You do not need to reinstall packages.

If you see `No module named 'flask'`, activate the WSL environment and install the app packages in that same terminal:

```bash
cd /mnt/c/SumoOverlay
source "$HOME/.venvs/sumo-overlay/bin/activate"
python -m pip install -e .
python app.py
```

Do not use `sudo pip install flask`: it installs into the system Python, which may not be the Python running this app. If `python3.11` is unavailable, refresh AlmaLinux's package list with `sudo dnf makecache`, then retry `sudo dnf install -y python3.11 python3.11-pip`. Check that AlmaLinux 9 software repositories are enabled if DNF still cannot find the packages.

With WSL 2 localhost forwarding enabled, Windows can open the app at `http://localhost:5000`. If Windows OBS cannot connect to the WSL server, use the Windows PowerShell setup above.

## Control page and addresses

With the app running, open:

- Control page: <http://127.0.0.1:5000/control> (WSL: <http://localhost:5000/control>)
- Main overlay: <http://127.0.0.1:5000/main-overlay> (WSL: <http://localhost:5000/main-overlay>)
- Pre-match overlay: <http://127.0.0.1:5000/overlay> (WSL: <http://localhost:5000/overlay>)
- Current bout JSON: <http://127.0.0.1:5000/api/state>
- Pre-match and H2H JSON: <http://127.0.0.1:5000/api/pre-match-state>
- Kanji-to-English lookup: <http://127.0.0.1:5000/api/lookup>
- Effective settings/reload status: <http://127.0.0.1:5000/api/settings>

Use **Previous Bout** and **Next Bout** for replay or manual synchronization. Enter a division number from 1 (Makuuchi) to 6 (Jonokuchi) and choose **Go to division** to jump to that division's first scheduled bout. This requires `division: 0` so all six divisions are loaded. The match-control toggle selects manual or automated control; automated bout detection will be wired to OCR in a future change.

Use **Auto-show duration** and **Save duration** to set how long the pre-match card appears after a bout change. Set it to `0` to disable timed auto-show. **Show pre-match until I turn it off** pins the card on-screen until you press **Hide pre-match now**; this manual display remains available even when timed auto-show is disabled. These settings are saved in `config.json`.

## OBS Studio

In OBS, add two **Browser** sources and set each source's size to the `overlay.canvas` width and height in `config.json` (the included values are 1920 × 1080):

1. Main, always-on wrestler strip: `http://127.0.0.1:5000/main-overlay` (or `http://localhost:5000/main-overlay` when running in WSL).
2. Pre-match H2H card: `http://127.0.0.1:5000/overlay` (or `http://localhost:5000/overlay` when running in WSL).

In OBS, select the Browser source and drag it on the preview canvas, or right-click it and choose **Transform → Edit Transform** to set its position and size. This positions the whole transparent browser canvas; `overlay.main` and `overlay.pre_match` in `config.json` position their content inside that canvas.

Both pages have transparent backgrounds and update live. The main strip remains visible. When timed auto-show is enabled with a positive duration, the pre-match card appears after every bout change and hides when the timer expires, regardless of match-control mode. You can also pin it on from the Control page until you hide it; this manual display takes precedence over the timer. The pre-match page continues running in OBS, but its HTML element becomes hidden and the browser source is transparent between bouts; the source does not need to be removed or reloaded. The pages load the Socket.IO browser client from jsDelivr and poll local state if that script is unavailable.

## Head-to-head and replay safety

The H2H card uses sumo-api.com's `/api/rikishi/{id}/matches/{opponentId}` data. It counts only completed meetings before the selected bout, including earlier matches in the same basho, and excludes the selected bout and later bouts so a replay does not reveal its result. The arrow history is ordered oldest to newest; green left arrows indicate wins by the wrestler on the left, and purple right arrows indicate wins by the wrestler on the right. If an API response is incomplete or malformed, the pre-match endpoint reports an error rather than presenting a misleading record.

Basho records are calculated from completed earlier-day bouts and earlier bouts on the selected day. Winners and winning techniques are not sent to the overlays. If an earlier result is unavailable, the record is shown as unavailable rather than guessed.

## Tests and saved API data

Normal tests do not call sumo-api.com. They use the checked-in API response snapshot at `tests/fixtures/sumo-api-202505-day1.json`, containing bout cards for all six divisions and a real H2H response. The snapshot avoids network dependence and rate limits during routine test runs.

Install test dependencies and run the tests with:

**Windows PowerShell**

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
```

**AlmaLinux WSL**, after activating the virtual environment:

```bash
python -m pip install -e '.[test]'
python -m pytest
```

To explicitly run the API snapshot test against live sumo-api.com instead, add `--live-api`:

```powershell
.\.venv\Scripts\python.exe -m pytest --live-api
```

```bash
python -m pytest --live-api
```
