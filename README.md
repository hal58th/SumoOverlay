# Sumo Broadcast Overlay

This program downloads a basho's bout lists from [sumo-api.com](https://sumo-api.com), lets you move between bouts with a control page, and displays two OBS browser overlays:

1. **Main overlay** — a persistent, opaque scoresheet overlay with the current division, wrestler names, their records before this bout, and abbreviated ranks.
2. **Pre-match overlay** — a larger matchup card with ranks, pre-bout records, and head-to-head (H2H) wins. When enabled, it appears after a bout is selected and hides after the configured number of seconds. This timer works the same in manual and automated modes; set the duration to `0` or use the Control page toggle to turn it off.

Settings are read from `config.json`. The app watches that file and reloads valid changes while it is running. Optional OCR can recognize either or both Japanese names on ABEMA's scoresheet and select the uniquely matching scheduled bout automatically. OCR uses the exact Japanese-to-English lookup from sumo-api.com; it does not translate names.

## Requirements

- Python 3.10 or newer.
- An internet connection to install packages and fetch data from sumo-api.com.
- OBS Studio, if you want to stream the overlays.
- This project folder. The examples assume it is `C:\SumoOverlay`.

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
| `control.pre_match_salaried_seconds` | How long, in seconds, the pre-match overlay appears for Makuuchi and Juryo bouts. `0` disables timed auto-show for these salaried divisions. |
| `control.pre_match_unsalaried_seconds` | How long, in seconds, the pre-match overlay appears for Makushita and lower division bouts. `0` disables timed auto-show for these unsalaried divisions. |
| `control.pre_match_pinned` | `true` or `false`. When true, keeps the pre-match overlay visible until you hide it from the Control page. |
| `overlay.main.width`, `overlay.main.height` | Fixed pixel width and height for the main overlay and its OBS Browser Source. |
| `overlay.pre_match.width`, `overlay.pre_match.height` | Fixed pixel width and height for the pre-match overlay and its OBS Browser Source. |
| `overlay.pre_match.opacity` | Background opacity of the pre-match card, from `0` (transparent) to `1` (opaque). The default is `0.5`. Text opacity is unchanged. |
| `templates.main`, `templates.pre_match` | HTML filenames for the overlays. The app searches `custom_templates` first, then the built-in `templates` folder. |
| `ocr.enabled` | Enables the OCR scanner when the app is started normally. |
| `ocr.monitor` | MSS monitor number, starting at `1` for the first monitor (monitor `0` is the combined virtual desktop and is not used). |
| `ocr.interval_seconds` | Delay between OCR scans. |
| `ocr.stable_readings` | Number of consecutive matching reads required before selecting a bout. |
| `ocr.minimum_confidence` | Minimum EasyOCR confidence from `0` through `1` for each name. |
| `ocr.name_boxes.left`, `ocr.name_boxes.right` | Name rectangles in pixels relative to the top-left of `ocr.capture_regions.abema_scoresheet`. |
| `ocr.capture_regions.abema_scoresheet` | Rectangle around the ABEMA scoresheet, in pixels relative to the selected monitor. |

Each overlay's configured width and height define its complete fixed-size image in CSS pixels. There is no canvas scaling or internal positioning; place each Browser Source where you want it in OBS. Set `overlay.pre_match.opacity` from `0` to `1` to adjust only the card background transparency; the text stays fully opaque. Capture-region coordinates refer to the selected desktop monitor, not the OBS overlay. OCR name-box coordinates are pixel offsets inside the capture region. If ABEMA moves its scoresheet, update the capture rectangle and/or name-box coordinates to match.

`division: 0` loads all divisions by requesting each of the six supported divisions separately; the API does not provide a working all-divisions endpoint. Bouts are combined in broadcast order. Values 1 through 6 select one division; number 1 is the top division (Makuuchi) and number 6 is Jonokuchi. Use these numeric codes in `config.json`.

When you save a valid JSON file, the app applies changes within the configured watch interval. A change to the basho, division, or day reloads its bout data and selects the first bout. Changes to the OCR monitor, capture rectangle, name boxes, interval, and confidence are used on the next scan. Invalid JSON/settings or unavailable API data leave the last working settings active; the Control page reports a reload error. Fix the file and save again. Changing `server.host` or `server.port` requires restarting the app because the listening socket cannot be rebound while running.

## Per-window configuration overrides

Use a partial override JSON file when the ABEMA stream is shown in different window layouts or screen positions. The normal `config.json` remains the base configuration; keys in the override file replace only the matching values, including individual nested values. The app watches both files for changes. Keep shared settings in `config.json` and put only per-view differences in the override file.

For example, create `config.firefox.json` beside `config.json` with the capture rectangle and OCR name boxes measured for your Firefox window:

```json
{
  "ocr": {
    "capture_regions": {
      "abema_scoresheet": {
        "x": 0,
        "y": 0,
        "width": 1500,
        "height": 180
      }
    },
    "name_boxes": {
      "left": { "x": 198, "y": 50, "width": 400, "height": 40 },
      "right": { "x": 480, "y": 50, "width": 400, "height": 40 }
    }
  }
}
```

The example is only a template: use pixel coordinates measured for your monitor and browser layout. Name-box coordinates are relative to the top-left of the capture rectangle, and both boxes must fit inside it. You can also override other settings, such as `ocr.monitor`, `ocr.capture_regions.abema_scoresheet`, or `overlay.main.width`, using their normal `config.json` structure.

Start the app with the profile you want:

```powershell
.\.venv\Scripts\python.exe app.py --override-config .\config.firefox.json
```

For a different layout, make another file such as `config.fullscreen.json` and pass that filename instead. Without `--override-config`, the app uses `config.json` alone. The main `config.json` can still be selected separately with `--config`.

## OCR bout detection

OCR is opt-in at installation, but the sample `config.json` has `ocr.enabled` set to `true`. The scanner starts when you run `app.py`; it waits without capturing the screen while control mode is **manual**. To use it, install the optional packages, run the app on the same Windows desktop as the browser playing ABEMA, then choose **Automated** on the Control page. The page reports whether OCR is paused, reading, confirming names, below the confidence threshold, matched to a bout, or reporting an error. A separate **Last OCR action** line keeps the most recent match, no-match, low-confidence, or scan-error detail visible while the live status continues to update, so it can be copied for troubleshooting.

### Install OCR on Windows

Open PowerShell in the project folder and install the optional OCR dependencies into this project's virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"
```

EasyOCR also installs PyTorch, so this step can take several minutes and needs an internet connection. On its first OCR use, EasyOCR downloads its text-detection and Japanese-recognition models; allow those downloads to finish before the broadcast. This project sets PyTorch to two CPU threads and explicitly disables GPU use. The regular app and unit tests do not load the OCR model unless the live scanner is started.

Run `.\.venv\Scripts\python.exe app.py` from PowerShell on the Windows desktop where ABEMA is playing. Windows OBS can use `http://127.0.0.1:5000`.

### Calibrate OCR when the browser is not full-screen

The scanner captures one rectangle of the monitor, then crops the left and right wrestler names from that captured image before running OCR. The capture rectangle only needs to contain both name boxes; it does not need to include the whole scoresheet. The default example captures a 1920×1080 rectangle at the top-left of monitor 1. Change `ocr.capture_regions.abema_scoresheet` in `config.json` to a smaller screen-pixel rectangle containing both names. Its `x` and `y` start at the top-left of the selected monitor (not the whole multi-monitor desktop); `width` and `height` are the rectangle size. The full rectangle must fit on that monitor.

There is no drag-to-select calibration tool in the app yet. For now, measure the boxes from a screenshot and enter their coordinates in `config.json`. The name boxes use pixels (not percentages), so there is no percentage conversion:

1. Take a screenshot showing ABEMA at the same browser size and screen position you plan to use.
2. In an image editor that shows the cursor position and selection size, measure each wrestler's large, lower name line. Aim for the ring name only; avoid including the smaller pronunciation line above it.
3. Measure the smallest practical screen-pixel rectangle containing both name boxes. Enter it as `ocr.capture_regions.abema_scoresheet` (`x`, `y`, `width`, `height`). Its `x` and `y` are relative to the top-left corner of the selected monitor.
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

## Control page and addresses

With the app running, open:

- Control page: <http://127.0.0.1:5000/control>
- Scoreboard overlay: <http://127.0.0.1:5000/scoreboard-overlay>
- Pre-match overlay: <http://127.0.0.1:5000/overlay>
- Current bout JSON: <http://127.0.0.1:5000/api/state>
- Pre-match and H2H JSON: <http://127.0.0.1:5000/api/pre-match-state>
- Kanji-to-English lookup: <http://127.0.0.1:5000/api/lookup>
- OCR status: <http://127.0.0.1:5000/api/ocr/status>
- Effective settings/reload status: <http://127.0.0.1:5000/api/settings>

Use **Previous Bout** and **Next Bout** for replay or manual synchronization. After a bout finishes, choose **East Wins** or **West Wins** to show that result in the Control page and OBS overlays. This temporarily adds one win to the selected wrestler and one loss to the other wrestler's displayed pre-match record; those two updated counts turn red and keep their normal division-based bold emphasis. The result is held in memory only, makes no sumo-api.com request, and is cleared when the selected bout changes. Enter a division number from 1 (Makuuchi) to 6 (Jonokuchi) and choose **Go to division** to jump to that division's first scheduled bout. This requires `division: 0` so all six divisions are loaded. Set match control to **Manual** for button-driven selection or **Automated** for OCR selection. The OCR status appears below the mode control.

Use the separate **Salaried divisions** and **Unsalaried divisions** duration fields and click **Save duration** to choose how long the pre-match card appears after a bout change. Salaried covers Makuuchi and Juryo; unsalaried covers Makushita, Sandanme, Jonidan, and Jonokuchi. Set either value to `0` to disable timed auto-show for that group. **Show pre-match until I turn it off** pins the card on-screen until you press **Hide pre-match now**; manual display remains available even when timed auto-show is disabled. These settings are saved in `config.json`.

## OBS Studio

In OBS, add two **Browser** sources:

1. Scoreboard, always-on scoresheet overlay: `http://127.0.0.1:5000/scoreboard-overlay`. Set the Browser Source width and height to `overlay.main.width` and `overlay.main.height` in `config.json`.
2. Pre-match H2H card: `http://127.0.0.1:5000/overlay`. Set its Browser Source width and height to `overlay.pre_match.width` and `overlay.pre_match.height`.

In OBS, position each Browser Source on the scene as desired. The overlay content is rendered at the configured dimensions in fixed CSS pixels and does not scale to fit the browser viewport. To change an overlay's rendered size, edit its width and height in `config.json`, then use those same values for the OBS Browser Source dimensions.

Both pages update live. The main overlay has a solid opaque background so it fully covers the scoresheet; the pre-match card remains translucent, and the rest of each page is transparent. Main overlay text uses fixed pixel sizes; long wrestler names remain on one line and are truncated with an ellipsis instead of shrinking. If the main Browser source is smaller than the fixed CSS dimensions, its content will be clipped. When timed auto-show is enabled with a positive duration, the pre-match card appears after every bout change and hides when the timer expires, regardless of match-control mode. You can also pin it on from the Control page until you hide it; this manual display takes precedence over the timer. The pre-match page continues running in OBS, but its HTML element becomes hidden and the browser source is transparent between bouts; the source does not need to be removed or reloaded. The pages load the Socket.IO browser client from jsDelivr and poll local state if that script is unavailable.

## Custom overlay templates

`config.json` selects the HTML files for the main and pre-match overlays with `templates.main` and `templates.pre_match`. By default these select the built-in pages in `templates`. To customize an overlay, copy its starter HTML file into `custom_templates`, edit the copy, and point the corresponding config value to its filename. The custom folder is searched before the built-in templates, and the Control/admin page remains unchanged. See [custom_templates/README.md](custom_templates/README.md) for step-by-step instructions.

## Head-to-head and replay safety

The H2H card uses sumo-api.com's `/api/rikishi/{id}/matches/{opponentId}` data. It counts only completed meetings before the selected bout, including earlier matches in the same basho, and excludes the selected bout and later bouts so a replay does not reveal its result. The arrow history is ordered oldest to newest; green left arrows indicate wins by the wrestler on the left, and purple right arrows indicate wins by the wrestler on the right. Each wrestler's career record, winning percentage, and completed match count are calculated from their career match history, excluding the selected tournament day and later results. The higher career win percentage is bolded; career match counts, wrestling tenure, height, and weight are compared and bolded independently when the value is greater. Tied or unavailable values are not emphasized.

The card also shows each wrestler's age, height, weight, stable (`heya`), and place of origin (`shusshin`) when available, with one labeled detail per line. Age is shown as completed years and months, rounded down, using the computer's current date. Wrestling tenure is calculated from the debut basho to the selected basho and shown in years and months. Height is shown in centimeters and feet/inches; weight is shown in kilograms and pounds. Profile details come from sumo-api.com's `/api/rikishi/{id}` endpoint and are cached for the running app. Stable, height, weight, and origin are the profile's latest values, not historical values from the selected replay date. Missing optional profile fields are omitted. If an API response is incomplete or malformed, the pre-match endpoint reports an error rather than presenting a misleading record.

Basho records are calculated from completed earlier-day bouts and earlier bouts on the selected day. In the Control, main, and pre-match overlays, wins and losses counts are emphasized independently: 8 or more in Makuuchi and Juryo, and 4 or more in Makushita, Sandanme, Jonidan, and Jonokuchi. Emphasized counts are rendered larger, heavier, and brighter so they stand out. Official winners and winning techniques are not fetched from or sent by the schedule API; the Control page's manual winner buttons are temporary audience display overrides. If an earlier result is unavailable, the record is shown as unavailable rather than guessed.

## Tests and saved API data

Normal tests do not call sumo-api.com or load the OCR model. They use the checked-in API response snapshot at `tests/fixtures/sumo-api-202505-day1.json`, containing bout cards for all six divisions and a real H2H response. The sample ABEMA images in `tests/fixtures/ocr/` are local references for checking crop placement and calibrating OCR without accessing the live broadcast. The snapshot avoids network dependence and rate limits during routine test runs.

Install test dependencies and run the tests with:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
```

To explicitly run the API snapshot test against live sumo-api.com instead, add `--live-api`:

```powershell
.\.venv\Scripts\python.exe -m pytest --live-api
```
