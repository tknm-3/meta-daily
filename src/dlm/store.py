"""Persistent state: which deck ids we've already notified about.

Stored as data/seen.json and committed back by the workflow, so each cron run
knows what is genuinely new. The id list is capped to keep the file bounded;
DLM ids are time-ordered (Mongo ObjectIds), so trimming the oldest is safe.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "seen.json"
MAX_IDS = 8000


class SeenStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path
        self._ids: list[str] = []
        self._set: set[str] = set()
        self.first_run = True
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self._ids = list(data.get("seen_ids", []))
                self._set = set(self._ids)
                self.first_run = not self._set
            except (json.JSONDecodeError, OSError):
                self.first_run = True

    def has(self, deck_id: str) -> bool:
        return deck_id in self._set

    def add_many(self, deck_ids) -> None:
        for deck_id in deck_ids:
            if deck_id and deck_id not in self._set:
                self._set.add(deck_id)
                self._ids.append(deck_id)
        if len(self._ids) > MAX_IDS:
            drop = self._ids[: len(self._ids) - MAX_IDS]
            self._ids = self._ids[-MAX_IDS:]
            self._set.difference_update(drop)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "count": len(self._ids),
            "seen_ids": self._ids,
        }
        self.path.write_text(json.dumps(payload, indent=2))
