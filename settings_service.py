"""App settings stored in SQLite."""

from __future__ import annotations

from storage import Storage


class SettingsService:
    DEFAULTS = {
        "default_model": "auto",
        "default_reasoning": "",
        "default_mode": "interactive",
        "telemetry_enabled": "0",
        "auto_approve": "0",
    }

    def __init__(self, storage: Storage):
        self.storage = storage

    def get_settings(self) -> dict:
        rows = self.storage.query("SELECT key, value FROM settings")
        data = dict(self.DEFAULTS)
        data.update({r["key"]: r["value"] for r in rows})
        return data

    def update_settings(self, patch: dict) -> dict:
        for key, value in (patch or {}).items():
            if key in self.DEFAULTS:
                self.storage.set_setting(key, value)
        return self.get_settings()
