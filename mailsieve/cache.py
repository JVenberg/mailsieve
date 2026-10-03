"""Jev answers keyed by (message id, question fingerprint): SQLite locally, Firestore in Cloud Run.

Rules and cutoffs are re-evaluated from cached answers, so tuning them never re-asks Jev; a reworded
question gets a new fingerprint and only that question is asked again.
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

LOCAL_PATH = Path(__file__).parent.parent / ".cache" / "answers.db"


class SqliteCache:
    def __init__(self, path: Path = LOCAL_PATH):
        path.parent.mkdir(exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute(
            "create table if not exists answers ("
            "msg_id text, fingerprint text, question text, answer text, asked_at real,"
            "primary key (msg_id, fingerprint))"
        )

    def get(self, msg_id: str, fingerprints: dict[str, str]) -> dict:
        """fingerprints: question id -> fingerprint. Returns question id -> answer for the cached ones."""
        by_fp = {fp: qid for qid, fp in fingerprints.items()}
        with self.lock:
            rows = self.db.execute(
                f"select fingerprint, answer from answers where msg_id = ? and fingerprint in "
                f"({','.join('?' * len(by_fp))})",
                [msg_id, *by_fp],
            ).fetchall()
        return {by_fp[fp]: json.loads(a) for fp, a in rows}

    def put(self, msg_id: str, answers: dict, fingerprints: dict[str, str]) -> None:
        now = time.time()
        with self.lock, self.db:
            self.db.executemany(
                "insert or replace into answers values (?, ?, ?, ?, ?)",
                [(msg_id, fingerprints[q], q, json.dumps(a), now) for q, a in answers.items()],
            )


class FirestoreCache:
    def __init__(self, collection: str = "answers"):
        from google.cloud import firestore

        self.client = firestore.Client()
        self.col = self.client.collection(collection)

    def get(self, msg_id: str, fingerprints: dict[str, str]) -> dict:
        refs = {self.col.document(f"{msg_id}:{fp}"): qid for qid, fp in fingerprints.items()}
        return {refs[d.reference]: d.get("answer") for d in self.client.get_all(list(refs)) if d.exists}

    def put(self, msg_id: str, answers: dict, fingerprints: dict[str, str]) -> None:
        batch = self.client.batch()
        for q, a in answers.items():
            doc = {"msg_id": msg_id, "question": q, "answer": a, "asked_at": time.time()}
            batch.set(self.col.document(f"{msg_id}:{fingerprints[q]}"), doc)
        batch.commit()


def open_cache():
    return FirestoreCache() if os.environ.get("CACHE") == "firestore" else SqliteCache()
