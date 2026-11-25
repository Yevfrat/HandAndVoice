"""Loads/saves app aliases (app_aliases.json) and web aliases (web_aliases.json).

Every app entry is independent: its alias list belongs to that entry only.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from services.config_store import ConfigStore

log = logging.getLogger(__name__)

APP_KINDS = ("exe", "lnk", "uwp", "url")


def normalize_entry(raw: Any) -> Optional[dict]:
    """Return a clean copy of an alias entry, or None if it is unusable."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    if not name:
        return None
    aliases: list[str] = []
    for a in raw.get("aliases", []) or []:
        a = str(a).strip()
        if a and a.lower() != name.lower() and a.lower() not in (x.lower() for x in aliases):
            aliases.append(a)
    kind = str(raw.get("kind", "exe")).strip().lower()
    if kind not in APP_KINDS:
        kind = "exe"
    return {
        "name": name,
        "aliases": aliases,
        "target": str(raw.get("target", "")).strip(),
        "kind": kind,
        "arguments": str(raw.get("arguments", "")).strip(),
    }


class AliasManager:
    def __init__(self, store: ConfigStore):
        self.store = store

    # ----- apps -----
    def load_app_aliases(self) -> list[dict]:
        data = self.store.load_json("app_aliases.json")
        out = []
        for raw in data if isinstance(data, list) else []:
            entry = normalize_entry(raw)
            if entry:
                out.append(entry)
            else:
                log.warning("Skipping invalid app alias entry")
        return out

    def save_app_aliases(self, entries: list[dict]) -> None:
        clean = [e for e in (normalize_entry(x) for x in entries) if e]
        self.store.save_json("app_aliases.json", clean)

    def upsert_app_alias(self, entry: dict, replace_name: Optional[str] = None) -> None:
        new = normalize_entry(entry)
        if new is None:
            raise ValueError("An app entry needs a name")
        entries = self.load_app_aliases()
        target_name = (replace_name or new["name"]).lower()
        for i, e in enumerate(entries):
            if e["name"].lower() == target_name:
                entries[i] = new
                break
        else:
            entries.append(new)
        self.save_app_aliases(entries)

    def delete_app_alias(self, name: str) -> None:
        self.save_app_aliases([e for e in self.load_app_aliases() if e["name"].lower() != name.lower()])

    # ----- web -----
    def load_web_aliases(self) -> dict[str, str]:
        data = self.store.load_json("web_aliases.json")
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def save_web_aliases(self, aliases: dict[str, str]) -> None:
        self.store.save_json("web_aliases.json", aliases)
