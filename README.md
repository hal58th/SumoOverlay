# Sumo Broadcast Overlay

This program downloads a basho's bout lists from [sumo-api.com](https://sumo-api.com), lets you move between bouts with a control page, and displays two OBS browser overlays:

1. **Main overlay** — a compact, persistent strip in the top-left with the current division, wrestler names, their records before this bout, and abbreviated ranks.
2. **Pre-match overlay** — a larger matchup card with ranks, pre-bout records, and head-to-head (H2H) wins. When enabled, it appears after a bout is selected and hides after the configured number of seconds. This timer works the same in manual and automated modes; set the duration to `0` or use the Control page toggle to turn it off.

Settings are read from `config.json`; the program does not use environment variables. The app watches that file and reloads valid changes while it is running. Optional OCR can recognize either or both Japanese names on ABEMA's scoresheet and select the uniquely matching scheduled bout automatically. OCR uses the exact Japanese-to-English lookup from sumo-api.com; it does not translate names.

## Requirements

- Python 3.10 or newer.
- An internet connection to install packages and fetch data from sumo-api.com.
- OBS Studio, if you want to stream the overlays.
- This project folder. The examples assume it is `C:\SumoOverlay` in Windows, or `/mnt/c/SumoOverlay` from WSL.

The base app uses CPU-only Python packages. OCR is optional and requires EasyOCR, PyTorch, MSS, and NumPy; a GPU is not required.

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
| `control.mode` | `"manual"` or `"automated"`. Manual mode uses the Control page buttons. Automated mode lets OCR select a uniquely identified bout when either one or both names meet the confidence threshold for the configured number of consecutive reads. |
| `control.pre_match_seconds` | How long, in seconds, the pre-match overlay appears after a bout change. `0` disables timed auto-show. |
| `control.pre_match_pinned` | `true` or `false`. When true, keeps the pre-match overlay visible until you hide it from the Control page. |
| `overlay.canvas` | The OBS browser-source design width and height, in pixels. |
| `overlay.main` | Main overlay rectangle: top-left `x`, `y`, `width`, and `height`, in pixels on the canvas. It centers each wrestler's name over their rank and record (shown side by side); division and “VS” are in the middle. |
| `overlay.pre_match` | Pre-match overlay rectangle, using the same pixel coordinate system. The default covers almost the full canvas. |
| `templates.main`, `templates.pre_match` | HTML filenames for the overlays. The app searches `custom_templates` first, then the built-in `templates` folder. |
| `ocr.enabled` | Enables the OCR scanner when the app is started normally. |
| `ocr.monitor` | MSS monitor number, starting at `1` for the first monitor (monitor `0` is the combined virtual desktop and is not used). |
| `ocr.interval_seconds` | Delay between OCR scans. |
| `ocr.stable_readings` | Number of consecutive matching reads required before selecting a bout. |
| `ocr.minimum_confidence` | Minimum EasyOCR confidence from `0` through `1` for each name. |
| `ocr.name_boxes.left`, `ocr.name_boxes.right` | Name rectangles in pixels relative to the top-left of `capture_regions.abema_scoresheet`. |
| `capture_regions.abema_scoresheet` | Rectangle around the ABEMA scoresheet, in pixels relative to the selected monitor. |

Both overlay rectangles must fit within `overlay.canvas`. Capture-region coordinates refer to the selected desktop monitor, not the OBS overlay canvas. OCR name-box coordinates are pixel offsets inside that capture region. If ABEMA moves its scoresheet, update the capture rectangle and/or name-box coordinates to match. The main and pre-match overlay rectangles can be adjusted separately. The default pre-match rectangle is almost full screen; the card background is 50% opaque so the broadcast remains visible behind it while the text stays fully readable.

`division: 0` loads all divisions by requesting each of the six supported divisions separately; the API does not provide a working all-divisions endpoint. Bouts are combined in broadcast order. Values 1 through 6 select one division; number 1 is the top division (Makuuchi) and number 6 is Jonokuchi. Use these numeric codes in `config.json`.

When you save a valid JSON file, the app applies changes within the configured watch interval. A change to the basho, division, or day reloads its bout data and selects the first bout. Changes to the OCR monitor, capture rectangle, name boxes, interval, and confidence are used on the next scan. Invalid JSON/settings or unavailable API data leave the last working settings active; the Control page reports a reload error. Fix the file and save again. Changing `server.host` or `server.port` requires restarting the app because the listening socket cannot be rebound while running.

## Per-window configuration overrides

Use a partial override JSON file when the ABEMA stream is shown in different window layouts or screen positions. The normal `config.json` remains the base configuration; keys in the override file replace only the matching values, including individual nested values. The app watches both files for changes. Keep shared settings in `config.json` and put only per-view differences in the override file.

For example, create `config.firefox.json` beside `config.json` with the capture rectangle and OCR name boxes measured for your Firefox window:

```json
{
  "capture_regions": {
    "abema_scoresheet": {
      "x": 120,
      "y": 80,
      "width": 1280,
      "height": 720
    }
  },
  "ocr": {
    "name_boxes": {
      "left": { "x": 198, "y": 25, "width": 200, "height": 40 },
      "right": { "x": 480, "y": 25, "width": 200, "height": 40 }
    }
  }
}
```

The example is only a template: use pixel coordinates measured for your monitor and browser layout. Name-box coordinates are relative to the top-left of the capture rectangle, and both boxes must fit inside it. You can also override other settings, such as `ocr.monitor` or `overlay.main`, using their normal `config.json` structure.

Start the app with the profile you want:

```powershell
.\.venv\Scripts\python.exe app.py --override-config .\config.firefox.json
```

For a different layout, make another file such as `config.fullscreen.json` and pass that filename instead. Without `--override-config`, the app uses `config.json` alone. The main `config.json` can still be selected separately with `--config`.

## OCR bout detection

OCR is opt-in at installation, but the sample `config.json` has `ocr.enabled` set to `true`. The scanner starts when you run `app.py`; it waits without capturing the screen while control mode is **manual**. To use it, install the optional packages, run the app on the same Windows desktop as the browser playing ABEMA, then choose **Automated** on the Control page. The page reports whether OCR is paused, reading, confirming names, below the confidence threshold, matched to a bout, or reporting an error.

### Install OCR on Windows

Open PowerShell in the project folder and install the optional OCR dependencies into this project's virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"
```

EasyOCR also installs PyTorch, so this step can take several minutes and needs an internet connection. On its first OCR use, EasyOCR downloads its text-detection and Japanese-recognition models; allow those downloads to finish before the broadcast. This project sets PyTorch to two CPU threads and explicitly disables GPU use. The regular app and unit tests do not load the OCR model unless the live scanner is started.

Run `.\.venv\Scripts\python.exe app.py` from PowerShell. Run the app in Windows because MSS must capture the Windows monitor where ABEMA is playing. WSL has its own Linux screen session and cannot capture a Windows browser window. Windows OBS can use `http://127.0.0.1:5000` when the app runs in Windows.

### Calibrate OCR when the browser is not full-screen

The scanner captures one rectangle of the monitor, then crops the left and right wrestler names from that captured image before running OCR. The capture rectangle only needs to contain both name boxes; it does not need to include the whole scoresheet. The default example captures a 1920×1080 rectangle at the top-left of monitor 1. Change `capture_regions.abema_scoresheet` in `config.json` to a smaller screen-pixel rectangle containing both names. Its `x` and `y` start at the top-left of the selected monitor (not the whole multi-monitor desktop); `width` and `height` are the rectangle size. The full rectangle must fit on that monitor.

There is no drag-to-select calibration tool in the app yet. For now, measure the boxes from a screenshot and enter their coordinates in `config.json`. The name boxes use pixels (not percentages), so there is no percentage conversion:

1. Take a screenshot showing ABEMA at the same browser size and screen position you plan to use.
2. In an image editor that shows the cursor position and selection size, measure each wrestler's large, lower name line. Aim for the ring name only; avoid including the smaller pronunciation line above it.
3. Measure the smallest practical screen-pixel rectangle containing both name boxes. Enter it as `capture_regions.abema_scoresheet` (`x`, `y`, `width`, `height`). Its `x` and `y` are relative to the top-left corner of the selected monitor.
4. For each name, subtract the capture rectangle's screen `x` and `y` from the name rectangle's screen `x` and `y`. Use those relative `x` and `y` offsets, plus the measured pixel width and height, in `ocr.name_boxes.left` and `ocr.name_boxes.right`. Both name boxes must fit inside the capture rectangle.

These annotated examples show the capture rectangle and individual name crops. Their coordinates describe the sample images only; measure your own monitor and browser layout:

- `match_a.png`: [OCR calibration guide](./match_a_ocr_guide.png)
- `match_b.png`: [OCR calibration guide with a tight capture rectangle](./match_b_ocr_guide.png)

For example, if the capture rectangle starts at screen `(100, 50)` and a wrestler's lower name line starts at `(260, 70)` with a size of `130 × 45` pixels, the name box is `x: 160, y: 20, width: 130, height: 45`. The subtraction is simply `(260 - 100, 70 - 50)`. Enter it like this:

```json
"name_boxes": {
  "left":  { "x": 160, "y": 20, "width": 130, "height": 45 },
  "right": { "x": 400, "y": 20, "width": 130, "height": 45 }
}
```

`x: 0, y: 0` is the capture rectangle's top-left pixel. The name-box width and height are also pixels. For the same example, if the right wrestler's name starts at `(500, 70)` and measures `130 × 45` pixels, its relative box is `x: 400, y: 20, width: 130, height: 45`. The name boxes must fit inside the capture rectangle. Save `config.json`; the running app applies the new coordinates automatically. Keep each full wrestler name inside its box when possible, but avoid including the neighboring name or scoresheet labels. If the web player moves, resize it, or changes its layout, recalibrate the capture rectangle and name boxes.

OCR waits for the configured number of consecutive reads before acting. Either wrestler's recognized name may be enough: recognized names are resolved through the API's Japanese-name dictionary, then the scanner selects a bout only if that name (or both names together) uniquely identifies one bout in the loaded basho, day, and division schedule. A single name that appears in more than one scheduled bout remains ambiguous and will not trigger a guess. The scanner will not machine-translate unknown names. Use `division: 0` to load every division when the broadcast can move between divisions. The automatic detector can identify the bout already on screen, so it also supports starting an offline replay in the middle of the schedule.

Japanese OCR can confuse similar-looking or uncommon kanji. If the Control page says the recognized names are not in the schedule, inspect the displayed OCR text and recalibrate the name boxes or improve the captured video size/clarity. Do not lower the confidence threshold just to force a match: the app only changes bouts after exact API dictionary resolution and a unique schedule match.

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

WSL is a Linux environment that runs alongside Windows. It has its own Linux Python and packages, separate from Windows. For OCR, run the app with Windows Python from PowerShell: MSS in WSL cannot capture the Windows desktop/browser, even if you install Linux X11 libraries. WSL remains an option for serving the overlays with manual bout controls.

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

To serve the overlays from WSL without OCR, open `config.json` and set these values:

```json
"control": {
  "mode": "manual"
},
"ocr": {
  "enabled": false
}
```

Keep the other settings in those objects, including the commas and braces. Then, from the project folder, install the app and run it:

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

If OCR is enabled while running in WSL, the app reports that it cannot capture the Windows desktop. Installing `libxcb` only supplies an X11 library; it does not give WSL's MSS access to the Windows browser window. To use OCR with ABEMA in a Windows browser, start the app from PowerShell using the Windows instructions above and keep `ocr.enabled` set to `true`.

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
- OCR status: <http://127.0.0.1:5000/api/ocr/status>
- Effective settings/reload status: <http://127.0.0.1:5000/api/settings>

Use **Previous Bout** and **Next Bout** for replay or manual synchronization. Enter a division number from 1 (Makuuchi) to 6 (Jonokuchi) and choose **Go to division** to jump to that division's first scheduled bout. This requires `division: 0` so all six divisions are loaded. Set match control to **Manual** for button-driven selection or **Automated** for OCR selection. The OCR status appears below the mode control.

Use **Auto-show duration** and **Save duration** to set how long the pre-match card appears after a bout change. Set it to `0` to disable timed auto-show. **Show pre-match until I turn it off** pins the card on-screen until you press **Hide pre-match now**; this manual display remains available even when timed auto-show is disabled. These settings are saved in `config.json`.

## OBS Studio

In OBS, add two **Browser** sources and set each source's size to the `overlay.canvas` width and height in `config.json` (the included values are 1920 × 1080):

1. Main, always-on wrestler strip: `http://127.0.0.1:5000/main-overlay` (or `http://localhost:5000/main-overlay` when running in WSL).
2. Pre-match H2H card: `http://127.0.0.1:5000/overlay` (or `http://localhost:5000/overlay` when running in WSL).

In OBS, select the Browser source and drag it on the preview canvas, or right-click it and choose **Transform → Edit Transform** to set its position and size. This positions the whole transparent browser canvas; `overlay.main` and `overlay.pre_match` in `config.json` position their content inside that canvas.

Both pages have transparent backgrounds and update live. The main strip remains visible. Wrestler names automatically shrink to a 10px minimum to fit their available width; if a name is still too long, it stays on one line and is truncated with an ellipsis. This sizing is recalculated when the overlay or browser source is resized. When timed auto-show is enabled with a positive duration, the pre-match card appears after every bout change and hides when the timer expires, regardless of match-control mode. You can also pin it on from the Control page until you hide it; this manual display takes precedence over the timer. The pre-match page continues running in OBS, but its HTML element becomes hidden and the browser source is transparent between bouts; the source does not need to be removed or reloaded. The pages load the Socket.IO browser client from jsDelivr and poll local state if that script is unavailable.

## Custom overlay templates

`config.json` selects the HTML files for the main and pre-match overlays with `templates.main` and `templates.pre_match`. By default these select the built-in pages in `templates`. To customize an overlay, copy its starter HTML file into `custom_templates`, edit the copy, and point the corresponding config value to its filename. The custom folder is searched before the built-in templates, and the Control/admin page remains unchanged. See [custom_templates/README.md](custom_templates/README.md) for step-by-step instructions.

## Head-to-head and replay safety

The H2H card uses sumo-api.com's `/api/rikishi/{id}/matches/{opponentId}` data. It counts only completed meetings before the selected bout, including earlier matches in the same basho, and excludes the selected bout and later bouts so a replay does not reveal its result. The arrow history is ordered oldest to newest; green left arrows indicate wins by the wrestler on the left, and purple right arrows indicate wins by the wrestler on the right. Each wrestler's career record, winning percentage, and completed match count are calculated from their career match history, excluding the selected tournament day and later results. The higher pre-bout basho win percentage and career win percentage are bolded; career match counts, wrestling tenure, height, and weight are compared and bolded independently when the value is greater. Tied or unavailable values are not emphasized.

The card also shows each wrestler's age, height, weight, stable (`heya`), and place of origin (`shusshin`) when available, with one labeled detail per line. Age is shown as completed years and months, rounded down, using the computer's current date. Wrestling tenure is calculated from the debut basho to the selected basho and shown in years and months. Height is shown in centimeters and feet/inches; weight is shown in kilograms and pounds. Profile details come from sumo-api.com's `/api/rikishi/{id}` endpoint and are cached for the running app. Stable, height, weight, and origin are the profile's latest values, not historical values from the selected replay date. Missing optional profile fields are omitted. If an API response is incomplete or malformed, the pre-match endpoint reports an error rather than presenting a misleading record.

Basho records are calculated from completed earlier-day bouts and earlier bouts on the selected day. Winners and winning techniques are not sent to the overlays. If an earlier result is unavailable, the record is shown as unavailable rather than guessed.

## Tests and saved API data

Normal tests do not call sumo-api.com or load the OCR model. They use the checked-in API response snapshot at `tests/fixtures/sumo-api-202505-day1.json`, containing bout cards for all six divisions and a real H2H response. The sample ABEMA images in `tests/fixtures/ocr/` are local references for checking crop placement and calibrating OCR without accessing the live broadcast. The snapshot avoids network dependence and rate limits during routine test runs.

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
