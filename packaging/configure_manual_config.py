import sys
from pathlib import Path

import yaml


source = Path(sys.argv[1])
destination = Path(sys.argv[2])
settings = yaml.safe_load(source.read_text(encoding="utf-8"))
settings["control"]["mode"] = "manual"
settings["ocr"]["enabled"] = False
destination.write_text(
    yaml.safe_dump(settings, sort_keys=False, allow_unicode=True),
    encoding="utf-8",
)
