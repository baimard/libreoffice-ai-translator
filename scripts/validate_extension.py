#!/usr/bin/env python3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED = [
    "description.xml",
    "META-INF/manifest.xml",
    "Addons.xcu",
    "ProtocolHandler.xcu",
    "pythonpath/ai_translator.py",
    "description/desc_en.txt",
    "description/desc_fr.txt",
    "LICENSE",
]


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "extension")
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    if missing:
        raise SystemExit("Missing extension files: " + ", ".join(missing))
    for name in ("description.xml", "META-INF/manifest.xml", "Addons.xcu", "ProtocolHandler.xcu"):
        ET.parse(root / name)
    print("Extension structure is valid.")


if __name__ == "__main__":
    main()
