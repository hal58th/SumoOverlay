# Sumo Broadcast Overlay

This program downloads one day's sumo bout list from [sumo-api.com](https://sumo-api.com), lets you move between bouts with Previous/Next buttons, and shows English wrestler names, ranks, and pre-bout records in a transparent OBS overlay.

You do **not** need to know Python to follow these setup steps. Pick **one** way to run the program:

- **Windows PowerShell (recommended if you use OBS on Windows):** The app and OBS run on the same Windows machine.
- **AlmaLinux 9 in WSL:** Use this if you specifically want to run the app inside WSL. WSL is a Linux environment running alongside Windows. Its Python and installed packages are separate from Windows Python.

OCR/screen capture, H2H statistics, and the timed H2H-to-lower-third transition are not included in this prototype.

## What you need

- Python 3.10 or newer.
- An internet connection to download the Python packages and fetch sumo-api.com data.
- OBS Studio, if you want to stream the overlay.
- This project folder on your computer. The examples below assume it is `C:\SumoOverlay` in Windows, which is `/mnt/c/SumoOverlay` from WSL. If yours is somewhere else, use its actual location in the `cd` command.

The app uses CPU-only Python packages; EasyOCR, PyTorch, and a GPU are not required.

## Option 1: Windows PowerShell

### 1. Open PowerShell in the project folder

Open the Windows Start menu, type **PowerShell**, and open **Windows PowerShell** or **Terminal**. Enter:

```powershell
cd C:\SumoOverlay
```

If PowerShell says it cannot find that folder, change the command to the folder where you saved this project. The project folder should contain `app.py` and `pyproject.toml`. You can check by entering:

```powershell
Get-ChildItem
```

### 2. Check Python and create a private environment for this app

On Windows, the command `python` can point to a Microsoft Store shortcut even when Python is installed. Use the Python Launcher command `py` instead:

```powershell
py --version
py -0p
```

The first command prints the launcher's Python version. The second lists the Python versions installed on your computer. Choose any listed version that is 3.10 or newer. For example, if the list includes Python 3.11, create the app's environment like this:

```powershell
py -3.11 -m venv .venv
```

If your installed version is different, replace `3.11` with that version (for example, use `py -3.12` for Python 3.12). If `.venv` already exists, you can skip this creation command.

### 3. Install the app and its packages

Run these commands in the project folder:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

This installs Flask and the other required packages into this project's `.venv` folder only. It does not change the rest of your computer. Use the `.venv` Python for both installing and running the app; typing `python` by itself might select a different Python installation.

### 4. Choose the tournament and start the app

Set the tournament month, division, and day in the same PowerShell window:

```powershell
$env:SUMO_BASHO = "202609"
$env:SUMO_DIVISION = "Makuuchi"
$env:SUMO_DAY = "1"
.\.venv\Scripts\python.exe app.py
```

Change `202505` to the six-digit tournament ID (`YYYYMM`), and change the day to a number from `1` to `15`. The division must be one of `Makuuchi`, `Juryo`, `Makushita`, `Sandanme`, `Jonidan`, or `Jonokuchi`. The values above are an example. Keep the PowerShell window open while using the overlay; closing it stops the app. To stop the app, click the PowerShell window and press **Ctrl+C**.

Open these addresses in your Windows browser:

- Bout controls: <http://127.0.0.1:5000/control>
- OBS overlay: <http://127.0.0.1:5000/overlay>
- Kanji-to-English lookup: <http://127.0.0.1:5000/api/lookup>

### If PowerShell says Python is not installed

First try the `py` commands above. The project does not require the command to be named `python`.

If `py --version` works, but `python --version` opens the Microsoft Store or says Python is missing, keep using `py` to create `.venv` and the explicit `.\.venv\Scripts\python.exe` commands to install and run the app. You do not need to repair the `python` command.

If PowerShell says that `.\.venv\Scripts\python.exe` cannot be found, check that you are in the project folder with `Get-ChildItem`, then run the `py -3.11 -m venv .venv` command from step 2. Use the version number you actually have installed.

If `py` is also not recognized:

1. Open **Settings → Apps → Advanced app settings → App execution aliases**. If `python.exe` and `python3.exe` are enabled as App Installer aliases, turn those aliases off. This stops Windows from redirecting the `python` command to the Store.
2. Close PowerShell, open it again, and retry `py --version`.
3. If `py` is still unavailable, repair or install Python 3.11 or newer using the installer from [python.org](https://www.python.org/downloads/windows/). In the installer, select **Add python.exe to PATH** and, if offered, the Python Launcher. Close and reopen PowerShell after installation.

If `py -0p` lists several versions, use one that is 3.10 or newer when creating `.venv`; for example, `py -3.11 -m venv .venv`.

## Option 2: AlmaLinux 9 in WSL

### 1. Open AlmaLinux in WSL and go to the project

Open the **AlmaLinux 9** app from the Windows Start menu. You are now typing Linux commands, not PowerShell commands. If the project is in `C:\SumoOverlay`, enter:

```bash
cd /mnt/c/SumoOverlay
```

If your project is on another Windows drive, WSL mounts the drive under `/mnt` using a lowercase letter. For example, `D:\Projects\SumoOverlay` becomes `/mnt/d/Projects/SumoOverlay`.

Check that you are in the right place:

```bash
pwd
ls
```

The file list should include `app.py` and `pyproject.toml`.

### 2. Install Python 3.11 and create a WSL-only environment

In the AlmaLinux terminal, install Python 3.11 and its pip package manager:

```bash
sudo dnf install -y python3.11 python3.11-pip
```

Enter your Linux password if asked. Linux does not show characters while you type a password; type it and press **Enter**.

Create an environment in your Linux home folder, separate from any Windows `.venv`:

```bash
mkdir -p "$HOME/.venvs"
python3.11 -m venv "$HOME/.venvs/sumo-overlay"
source "$HOME/.venvs/sumo-overlay/bin/activate"
```

When it is active, your command prompt usually starts with `(sumo-overlay)`. Activating it tells Linux which Python to use.

### 3. Install the app and its packages inside WSL

Make sure you are still in the project folder, then run:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

These commands install Flask and the other packages into the WSL environment you just created. Windows and WSL do not share Python installations or installed packages. Installing Flask in Windows will not install it in WSL, and installing it in WSL will not install it in Windows.

### 4. Choose the tournament and start the app

In the same AlmaLinux terminal, set the tournament and start the program:

```bash
export SUMO_BASHO="202609"
export SUMO_DIVISION="Makuuchi"
export SUMO_DAY="1"
python app.py
```

Change `202505` to the six-digit tournament ID (`YYYYMM`), and change the day to a number from `1` to `15`. The division must be one of `Makuuchi`, `Juryo`, `Makushita`, `Sandanme`, `Jonidan`, or `Jonokuchi`. The values above are an example. Keep the terminal open while using the overlay; closing it stops the app. To stop the app, click the terminal and press **Ctrl+C**.

On a standard WSL 2 setup, Windows can open the app at:

- Bout controls: <http://localhost:5000/control>
- OBS overlay: <http://localhost:5000/overlay>
- Kanji-to-English lookup: <http://localhost:5000/api/lookup>

The `source .../activate` command only affects the current Linux terminal. If you close it and open another one, repeat the `cd`, `source`, and `export` commands before starting the app again. The environment and installed packages remain on disk; you do not need to reinstall them.

### If WSL says `No module named 'flask'`

This means the app is running with a Python environment that does not have the app's packages installed. In the **AlmaLinux terminal**:

1. Go to the project folder, for example `cd /mnt/c/SumoOverlay`.
2. Activate the WSL environment:

   ```bash
   source "$HOME/.venvs/sumo-overlay/bin/activate"
   ```

3. Install the app's packages in that environment:

   ```bash
   python -m pip install -e .
   ```

4. Start it from that same terminal with `python app.py`.

If DNF reports that it cannot find the Python packages, refresh its package list and try again:

```bash
sudo dnf makecache
sudo dnf install -y python3.11 python3.11-pip
```

If the packages are still unavailable, check that this terminal is AlmaLinux 9 and that its software repositories are enabled before continuing. Do not substitute an older system `python3` unless `python3 --version` reports 3.10 or newer. Do not use `sudo pip install flask`: that installs packages into a system Python and can leave the app using a different Python than the one you installed into.

To check which Python is active, activate the environment and run:

```bash
which python
python -m pip show Flask
```

The Python path should start with your home directory and end in `.venvs/sumo-overlay/bin/python`. If `pip show Flask` says the package was not found, install the project packages again with `python -m pip install -e .` while that environment is active.

If Windows cannot open `http://localhost:5000` while the server is running in WSL, use the Windows PowerShell setup above (recommended when OBS is on Windows), or check that your WSL installation is using WSL 2 and localhost forwarding is enabled. This app intentionally listens only on the local machine; do not expose it to your network by changing it to listen on all interfaces.

## OBS Studio

1. Start the app using either Windows or WSL, and open the Control page to check the bout.
2. In OBS, add a **Browser** source.
3. Set the URL to `http://127.0.0.1:5000/overlay` if the app is running in Windows, or `http://localhost:5000/overlay` if it is running in WSL.
4. Set the width to `1920` and height to `1080`.
5. Use **Previous Bout** and **Next Bout** on the Control page to sync a replay.

The overlay background is transparent. The page connects to the local app for bout updates; it falls back to local state polling if its Socket.IO browser library cannot load.

## Records and replay spoilers

sumo-api.com's bout-card response includes bout results, not a guaranteed pre-bout record. The app calculates each wrestler's record using completed bouts on earlier days and earlier bouts on the selected day. It does not send the selected bout's winner or winning technique to the overlay. If an earlier result is missing or unresolved, the app shows that record as unavailable instead of guessing and risking a spoiler.

## Optional settings

The defaults are `127.0.0.1` for the local server address and `5000` for its port. The API URL and timeout can also be changed with `SUMO_API_BASE_URL` and `SUMO_API_TIMEOUT`; `PORT` changes the local server port. Keep the server on the local machine; the control endpoints do not require a password.

## Running the tests (optional)

The tests are only needed if you are developing or changing the program.

**Windows PowerShell**, after setting up `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
```

**AlmaLinux WSL**, after activating the WSL environment:

```bash
python -m pip install -e '.[test]'
python -m pytest
```
