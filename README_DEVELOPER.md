## Install and run on Windows PowerShell

These instructions are recommended when OBS is running on Windows.

### 1. Open the project folder

Open PowerShell from the Windows Start menu and enter:

```powershell
cd C:\SumoOverlay
```

If the project is somewhere else, replace the path. `Get-ChildItem` should show `app.py` and `pyproject.toml`. Running the app creates `config.yaml` from defaults in `config.py` if it is missing.

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
# Upgrade pip to the latest version
.\.venv\Scripts\python.exe -m pip install --upgrade pip
# Install with OCR support (recommended):
.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"
# Install without OCR support:
.\.venv\Scripts\python.exe -m pip install -e .
```

Use the `.venv` Python shown in these commands to install and run the app. It installs Flask and the other app packages just for this project.

### 4. Start the app

Make any changes to `config.yaml`, then run:

```powershell
.\.venv\Scripts\python.exe app.py
```

Keep the PowerShell window open while using the overlays. Press **Ctrl+C** in that window to stop the app.

If PowerShell says `python` is not installed, use `py` and `.\.venv\Scripts\python.exe` as shown above; the standalone `python` command is not needed. If `py` is also not recognized, turn off the `python.exe` and `python3.exe` App Installer aliases under **Settings → Apps → Advanced app settings → App execution aliases**, reopen PowerShell, and try again. If Python is not installed, install Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/), selecting **Add python.exe to PATH** and the Python Launcher if offered.

## Tests and saved API data

Normal tests do not call sumo-api.com or load the OCR model. They use the checked-in API response snapshot at `tests/fixtures/sumo-api-202505-day1.json`, containing bout cards for all six divisions and a real H2H response. The sample sumo broadcast images in `tests/fixtures/ocr/` are local references for checking crop placement and calibrating OCR without accessing the live broadcast. The snapshot avoids network dependence and rate limits during routine test runs.

Install test dependencies and run the tests with:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
```

To explicitly run the API snapshot test against live sumo-api.com instead, add `--live-api`:

```powershell
.\.venv\Scripts\python.exe -m pytest --live-api
```

### Install OCR on Windows

Open PowerShell in the project folder and install the optional OCR dependencies into this project's virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"
```

EasyOCR also installs PyTorch, so this step can take several minutes and needs an internet connection. On its first OCR use, EasyOCR downloads its text-detection and Japanese-recognition models; allow those downloads to finish before the broadcast. This project sets PyTorch to two CPU threads and explicitly disables GPU use. The regular app and unit tests do not load the OCR model unless the live scanner is started.

Run `.\.venv\Scripts\python.exe app.py` from PowerShell on the Windows desktop where the sumo broadcast is playing.

## Building standalone Windows releases

To package distributable OCR and manual-only versions that run without a Python
installation, follow [packaging/README.md](packaging/README.md).

## Diagnostic Endpoints
Below are diagnostic endpoints for troubleshooting and inspecting the app's state:

- Current bout JSON: <http://127.0.0.1:5000/api/state>
- Pre-match and H2H JSON: <http://127.0.0.1:5000/api/pre-match-state>
- Kanji-to-English lookup: <http://127.0.0.1:5000/api/lookup>
- OCR status: <http://127.0.0.1:5000/api/ocr/status>
- Effective settings/reload status: <http://127.0.0.1:5000/api/settings>

## Head-to-head and replay safety

The H2H card uses sumo-api.com's `/api/rikishi/{id}/matches/{opponentId}` data. It counts only completed meetings before the selected bout, including earlier matches in the same basho, and excludes the selected bout and later bouts so a replay does not reveal its result. H2H history is fetched once per wrestler pair and cached for the lifetime of the loaded schedule, so repeated `/api/pre-match-state` requests and other updates reuse it; concurrent requests for the same pair also share one fetch. The cache is discarded when a new schedule is loaded. The arrow history is ordered oldest to newest; green left arrows indicate wins by the wrestler on the left, and purple right arrows indicate wins by the wrestler on the right. Each wrestler's career record, winning percentage, and completed match count are calculated from their career match history, excluding the selected tournament day and later results. The higher career win percentage is bolded; career match counts, wrestling tenure, height, and weight are compared and bolded independently when the value is greater. Tied or unavailable values are not emphasized.

The card also shows each wrestler's age, height, weight, stable (`heya`), and place of origin (`shusshin`) when available, with one labeled detail per line. Age is shown as completed years and months, rounded down, using the computer's current date. Wrestling tenure is calculated from the debut basho to the selected basho and shown in years and months. Height is shown in centimeters and feet/inches; weight is shown in kilograms and pounds. Profile details come from sumo-api.com's `/api/rikishi/{id}` endpoint and are cached for the running app. Stable, height, weight, and origin are the profile's latest values, not historical values from the selected replay date. Missing optional profile fields are omitted. If an API response is incomplete or malformed, the pre-match endpoint reports an error rather than presenting a misleading record.

Basho records are calculated from completed earlier-day bouts and earlier bouts on the selected day. In the Control, main, and pre-match overlays, wins and losses counts are emphasized independently: 8 or more in Makuuchi and Juryo, and 4 or more in Makushita, Sandanme, Jonidan, and Jonokuchi. Emphasized counts are rendered larger, heavier, and brighter so they stand out. Official winners and winning techniques are not fetched from or sent by the schedule API; the Control page's manual winner buttons are temporary audience display overrides. If an earlier result is unavailable, the record is shown as unavailable rather than guessed.