"""The only persistent runtime configuration store for AutomationAAE."""

import ipaddress
import sqlite3
import threading
from pathlib import Path
from urllib.parse import urlsplit


_EXACT_KEYS = frozenset({
    "ai.base_url", "ai.model_id", "ai.api_key", "ai.timeout_seconds",
    "ai.max_output_tokens", "ai.token_parameter", "audit.language", "bridge.pairing_token",
})
DEFAULTS = {
    "ai.base_url": "", "ai.model_id": "", "ai.api_key": "",
    "ai.timeout_seconds": 90, "ai.max_output_tokens": 3500,
    "ai.token_parameter": "max_tokens",
    "audit.language": "vi", "bridge.pairing_token": "",
}


def validate_base_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Base URL không hợp lệ.")
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError("Base URL không được chứa khoảng trắng, ký tự điều khiển hay dấu gạch chéo ngược.")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ValueError("Base URL không hợp lệ.") from error
    host = parsed.hostname
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL không được chứa thông tin đăng nhập, query hay fragment.")
    try:
        parsed.port
    except ValueError as error:
        raise ValueError("Cổng của Base URL không hợp lệ.") from error
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    loopback = host == "localhost" or (address is not None and address.is_loopback)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("Dùng HTTPS; HTTP chỉ được dùng cho gateway trên localhost.")
    if address is not None and (address.is_link_local or address.is_unspecified or address.is_multicast):
        raise ValueError("Không được dùng địa chỉ metadata/link-local/multicast.")
    if host in {"metadata.google.internal", "instance-data"}:
        raise ValueError("Địa chỉ gateway này không được phép.")
    if parsed.path.endswith("/chat/completions") or parsed.path.endswith("/models"):
        raise ValueError("Nhập Base URL (ví dụ https://gateway.example/v1), không nhập /chat/completions.")
    return value


def _validate_type_constraint(key, value):
    if key not in _EXACT_KEYS:
        raise ValueError("Khóa cấu hình không được hỗ trợ.")
    if key == "ai.base_url":
        return validate_base_url(value)
    if key in {"ai.timeout_seconds", "ai.max_output_tokens"}:
        bounds = (10, 180) if key == "ai.timeout_seconds" else (512, 16000)
        if type(value) is not int or not bounds[0] <= value <= bounds[1]:
            raise ValueError("Giá trị giới hạn AI không hợp lệ.")
        return value
    if not isinstance(value, str):
        raise ValueError("Giá trị cấu hình phải là chuỗi.")
    value = value.strip()
    if key == "audit.language" and value not in {"vi", "en"}:
        raise ValueError("Ngôn ngữ không được hỗ trợ.")
    if key == "ai.token_parameter" and value not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("Tham số giới hạn token không được hỗ trợ.")
    if key == "ai.api_key" and any(ord(char) > 126 for char in value):
        raise ValueError("API key phải dùng ký tự ASCII để gửi trong header Authorization.")
    maximum = 8192 if key == "ai.api_key" else 200
    if len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError("Giá trị cấu hình quá dài hoặc chứa ký tự điều khiển.")
    return value


class SettingsRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        self._connection.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._connection.commit()
        self.runtime = self.apply_settings()

    def apply_settings(self):
        with self._lock:
            values = dict(DEFAULTS)
            for key, raw in self._connection.execute("SELECT key, value FROM settings"):
                if key not in _EXACT_KEYS:
                    continue
                value = int(raw) if type(DEFAULTS[key]) is int else raw
                values[key] = _validate_type_constraint(key, value)
            self.runtime = values
            return dict(values)

    def get(self, key):
        with self._lock:
            if key not in _EXACT_KEYS:
                raise ValueError("Khóa cấu hình không được hỗ trợ.")
            return self.runtime[key]

    def public(self):
        with self._lock:
            return {**{key: value for key, value in self.runtime.items() if key != "ai.api_key"},
                    "bridge.pairing_token": self.runtime.get("bridge.pairing_token", ""),
                    "ai.has_api_key": bool(self.runtime["ai.api_key"])}

    def save(self, changes):
        if not isinstance(changes, dict) or not changes:
            raise ValueError("Không có cấu hình để lưu.")
        validated = {key: _validate_type_constraint(key, value) for key, value in changes.items()}
        with self._lock:
            with self._connection:
                self._connection.executemany(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    [(key, str(value)) for key, value in validated.items()],
                )
            self.runtime.update(validated)
            return self.public()

    def close(self):
        self._connection.close()
