"""Create a separate test copy of the MonkeyOCR document without settings."""

import json
import shutil
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATA = ROOT / "outputs" / "paper-reader" / "data"
SOURCE_DB = ROOT / "work" / "reader-before-reading-operations-20260923.db"
TARGET_DATA = ROOT / "work" / "reader-e2e-data"
DOC_ID = "f9091cd15d594b599bcf882010ec1479"


def main():
    TARGET_DATA.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SOURCE_DB) as source:
        row = source.execute(
            "SELECT fingerprint, body FROM docs WHERE id=?", (DOC_ID,)
        ).fetchone()
    if row is None:
        raise RuntimeError("Test document missing from backup")
    fingerprint, body = row
    document = json.loads(body)
    document["reading_page"] = 1
    with sqlite3.connect(TARGET_DATA / "reader.db") as target:
        target.execute(
            "CREATE TABLE IF NOT EXISTS docs "
            "(id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, body TEXT NOT NULL)"
        )
        target.execute(
            "INSERT OR REPLACE INTO docs VALUES(?,?,?)",
            (DOC_ID, fingerprint, json.dumps(document, ensure_ascii=False)),
        )
    shutil.copyfile(SOURCE_DATA / f"{DOC_ID}.pdf", TARGET_DATA / f"{DOC_ID}.pdf")
    print(f"Prepared isolated test document in {TARGET_DATA}")


if __name__ == "__main__":
    main()
