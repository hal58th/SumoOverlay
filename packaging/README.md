# Building Windows releases

This folder contains a repeatable Windows build for two standalone releases:

- **SumoOverlay** includes OCR and starts in automated mode.
- **SumoOverlay-NoOCR** excludes the OCR libraries and starts in manual mode.

Both releases are PyInstaller one-folder applications. Recipients do not need
Python installed. The release includes the app's configuration, templates,
custom template folder, license, and user documentation. The OCR version
downloads EasyOCR's Japanese recognition models the first time OCR is used, so
that first use requires an internet connection.

## Build prerequisites

1. Install 64-bit Python 3.11. This is the tested build version, including for
   the OCR release. Use the same architecture as the Windows computer on which
   the release will run.
2. Open PowerShell in the project folder.
3. Create and prepare the build environment:

   ```powershell
   py -3.11 -m venv .venv-build
   .\.venv-build\Scripts\python.exe -m pip install --upgrade pip
   .\.venv-build\Scripts\python.exe -m pip install -e ".[build]"
   ```

   To build the OCR release, also install its optional dependencies:

   ```powershell
   .\.venv-build\Scripts\python.exe -m pip install -e ".[ocr]"
   ```

   EasyOCR installs PyTorch and can take several minutes and several gigabytes
   of disk space. The OCR build runs on CPU; a GPU is not required. The
   manual-only build does not require the OCR dependencies.

## Build

From the project folder, run:

```powershell
.\packaging\build-releases.ps1
```

If PowerShell blocks the script because of the current execution policy, run
it in a process with a temporary policy override:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\packaging\build-releases.ps1
```

This applies only to that PowerShell process; it does not change the computer's
execution-policy setting.

By default this builds both releases. You can build just one:

```powershell
.\packaging\build-releases.ps1 -Variant ocr
.\packaging\build-releases.ps1 -Variant no-ocr
```

The script uses `.venv-build\Scripts\python.exe`, checks required dependencies,
builds clean one-folder applications, and creates ZIP archives:

- `dist\SumoOverlay\` and `dist\SumoOverlay.zip`
- `dist\SumoOverlay-NoOCR\` and `dist\SumoOverlay-NoOCR.zip`

To use another Python 3.11 build environment, pass its Python executable:

```powershell
.\packaging\build-releases.ps1 -PythonPath .venv-build\Scripts\python.exe
```

The script recreates the selected release folder and ZIP. Keep your own
customizations outside those generated folders before rebuilding.

## Try and distribute

Test each release from its extracted folder, not from the project checkout.
Run `SumoOverlay.exe` or `SumoOverlay-NoOCR.exe` and keep its console window
open while the overlays are in use. The server address and port come from the
included `config.yaml`. Open `http://127.0.0.1:5000/control` in a browser. Use
**Ctrl+C** in the console window to stop the server.

The `config.yaml` beside the executable is the live, editable configuration.
The no-OCR release starts with manual controls and OCR disabled. Do not move
only the `.exe`; keep the whole extracted folder together. Distribute the ZIP
for the desired variant.

These are Windows builds and should be built on Windows. The output is not a
code-signed installer; recipients may need to approve the download in Windows
or their antivirus software. The OCR archive is substantially larger because
it includes PyTorch and EasyOCR.
