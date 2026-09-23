"""Check that a PaperLoop upgrade preserved the existing reader records.

The snapshot is a SQLite online backup taken before the upgrade. New notes and
chats may be appended, but old entries and all translated blocks must survive.
"""

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "work" / "reader-before-reading-operations-20260923.db"
AFTER = ROOT / "outputs" / "paper-reader" / "data" / "reader.db"


def documents(path):
    with sqlite3.connect(path) as db:
        return {
            doc_id: json.loads(body)
            for doc_id, body in db.execute("SELECT id, body FROM docs")
        }


def main():
    old = documents(BEFORE)
    current = documents(AFTER)
    for doc_id, before in old.items():
        if doc_id not in current:
            raise AssertionError(f"Document disappeared: {doc_id}")
        after = current[doc_id]
        if before["blocks"] != after["blocks"]:
            raise AssertionError(f"Original/translated blocks changed: {doc_id}")
        for field in ("notes", "chats"):
            records = before[field]
            if records != after[field][: len(records)]:
                raise AssertionError(f"Existing {field} changed: {doc_id}")
        print(
            f"{doc_id}: preserved {len(before['blocks'])} blocks, "
            f"{len(before['notes'])} notes, and {len(before['chats'])} chats"
        )


if __name__ == "__main__":
    main()
