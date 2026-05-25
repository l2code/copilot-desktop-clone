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
        "gitlab_url": "",
        "gitlab_token": "",
        "gitlab_auth_type": "private-token",
        "gitlab_data_source": "rest",
        "gitlab_mcp_config": "",
        "gitlab_mcp_server": "GitLab-MCP",
        "gitlab_project": "",
        "gitlab_group": "",
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

    def get_gitlab_settings(self) -> dict:
        settings = self.get_settings()
        return {
            "url": settings.get("gitlab_url", ""),
            "auth_type": settings.get("gitlab_auth_type", "private-token"),
            "data_source": settings.get("gitlab_data_source", "rest"),
            "mcp_config": settings.get("gitlab_mcp_config", ""),
            "mcp_server": settings.get("gitlab_mcp_server", "GitLab-MCP"),
            "project": settings.get("gitlab_project", ""),
            "group": settings.get("gitlab_group", ""),
            "token_configured": bool(settings.get("gitlab_token")),
        }

    def update_gitlab_settings(self, patch: dict) -> dict:
        data = patch or {}
        mapping = {
            "url": "gitlab_url",
            "auth_type": "gitlab_auth_type",
            "data_source": "gitlab_data_source",
            "mcp_config": "gitlab_mcp_config",
            "mcp_server": "gitlab_mcp_server",
            "project": "gitlab_project",
            "group": "gitlab_group",
        }
        for public_key, setting_key in mapping.items():
            if public_key in data:
                value = str(data.get(public_key) or "").strip()
                if public_key == "auth_type" and value not in ("private-token", "bearer", "both"):
                    value = "private-token"
                if public_key == "data_source" and value not in ("rest", "mcp"):
                    value = "rest"
                self.storage.set_setting(setting_key, value)
        if data.get("clear_token"):
            self.storage.set_setting("gitlab_token", "")
        elif "token" in data and str(data.get("token") or "").strip():
            self.storage.set_setting("gitlab_token", str(data.get("token") or "").strip())
        return self.get_gitlab_settings()
