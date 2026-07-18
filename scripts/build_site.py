from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def main() -> int:
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "data").mkdir(parents=True)

    for name in ("index.html", "style.css", "script.js", "_headers"):
        shutil.copy2(ROOT / name, DIST / name)
    shutil.copy2(ROOT / "data" / "dashboard.json", DIST / "data" / "dashboard.json")
    print(f"built {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
