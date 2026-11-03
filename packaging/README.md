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

At startup, each executable generates its `config.yaml` from the defaults
embedded in `config.py`. The no-OCR executable selects manual mode and disables
OCR when creating a missing config.

To use another Python 3.11 build environment, pass its Python executable:

```powershell
.\packaging\build-releases.ps1 -PythonPath .venv-build\Scripts\python.exe
```

The script recreates the selected release folder and ZIP. Keep your own
customizations outside those generated folders before rebuilding.

## Publish a GitHub Release

The workflow in `.github/workflows/release.yml` builds and publishes the two
ZIP files whenever you push a version tag matching `v*`. It checks that the
tag points to a commit on `main`, runs the test suite, builds both Windows
variants, creates a published GitHub Release with generated notes, and attaches
`SumoOverlay.zip` and `SumoOverlay-NoOCR.zip`. You do not need to create the
release manually or add a personal access token.

First push the commit you want to release to `main`. Then, from the project
folder, create and push a new tag (replace `v1.0.0` with the next version):

```powershell
git switch main
git pull --ff-only origin main
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

In the repository on GitHub, open **Actions** to watch the build. When it
finishes, open **Releases** to find the published release and its two ZIP
downloads. A failed test or build prevents the release from being created.
Use a new version tag for each release.

GitHub Actions is free for standard runners in public repositories. Private
repositories receive a plan-dependent monthly allowance; usage beyond it can
be billed. See [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
for current allowances. GitHub Release assets are separate from Actions
artifacts: each release file must be under 2 GiB, and GitHub currently has no
total release-asset size or bandwidth limit. See
[About releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas).

## Try and distribute

Test each release from its extracted folder, not from the project checkout.
Run `SumoOverlay.exe` or `SumoOverlay-NoOCR.exe` and keep its console window
open while the overlays are in use. On first launch, the app creates
`config.yaml` beside the executable from the defaults embedded in `config.py`.
The server address and port come from `config.yaml`. Open
`http://127.0.0.1:5000/control` in a browser. Use **Ctrl+C** in the console
window to stop the server.

The `config.yaml` beside the executable is the live, editable configuration.
When updating an existing release, keep that file and extract the new release
over the existing folder (or copy the file into a newly extracted folder).
Startup adds settings missing from older configs while preserving existing
values; it removes only settings explicitly marked as deprecated by the app.
The no-OCR release starts with manual controls and OCR disabled. Do not move
only the `.exe`; keep the whole extracted folder together. Distribute the ZIP
for the desired variant.

These are Windows builds and should be built on Windows. The output is not a
code-signed installer; recipients may need to approve the download in Windows
or their antivirus software. The OCR archive is substantially larger because
it includes PyTorch and EasyOCR.
