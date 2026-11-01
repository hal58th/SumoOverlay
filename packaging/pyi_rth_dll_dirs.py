import os
import sys


_runtime_dll_paths = (
    sys._MEIPASS,
    os.path.join(sys._MEIPASS, "torch", "lib"),
)
_runtime_dll_directories = [
    os.add_dll_directory(path) for path in _runtime_dll_paths if os.path.isdir(path)
]
os.environ["PATH"] = os.pathsep.join(
    (*_runtime_dll_paths, os.environ.get("PATH", ""))
)
