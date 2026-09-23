"""Start the local worker independently of the desktop window."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ["PAPERLOOP_DATA"] = str(ROOT / "data")
(ROOT / "data").mkdir(exist_ok=True)
# Do not attach the server's output to the window: closing it must not break jobs.
log = open(ROOT / "data" / "desktop-backend.log", "a", encoding="utf-8", buffering=1)
sys.stdout = log
sys.stderr = log

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="127.0.0.1", port=8765, workers=1, access_log=False)
