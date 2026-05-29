import base64
import hashlib
import json
import os
import sys
import winreg
from dataclasses import dataclass, field

APP_NAME = "dekkr-slsk"
REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULT_PORT = 7431
DEFAULT_MAX_WORKERS = 2
DEFAULT_MIN_QUALITY_KBPS = 320
DEFAULT_RETRY_DELAY_MINUTES = 30
DEFAULT_BPM_THRESHOLD = 2.0


def _config_path() -> str:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    folder = os.path.join(appdata, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


def _exe_path() -> str:
    return sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])


def _derive_key() -> bytes:
    seed = (os.environ.get("COMPUTERNAME", "") + os.environ.get("USERNAME", "")).encode()
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())


def _encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    try:
        from cryptography.fernet import Fernet
        return Fernet(_derive_key()).encrypt(plaintext.encode()).decode()
    except Exception:
        return plaintext


def _decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        from cryptography.fernet import Fernet
        return Fernet(_derive_key()).decrypt(ciphertext.encode()).decode()
    except Exception:
        return ciphertext  # fallback si non chiffré ou clé différente


@dataclass
class Config:
    soulseek_username: str = ""
    soulseek_password: str = ""  # toujours en clair en mémoire
    download_folder: str = field(default_factory=lambda: os.path.join(
        os.path.expanduser("~"), "Music", "DekkR"
    ))
    accepted_formats: list = field(default_factory=lambda: ["mp3", "flac", "wav"])
    min_quality_kbps: int = DEFAULT_MIN_QUALITY_KBPS
    max_workers: int = DEFAULT_MAX_WORKERS
    retry_delay_minutes: int = DEFAULT_RETRY_DELAY_MINUTES
    bpm_threshold: float = DEFAULT_BPM_THRESHOLD
    port: int = DEFAULT_PORT
    autostart: bool = True
    analyzer_cloud_url: str = ""  # optionnel — fallback si bridge local absent
    analyzer_cloud_key: str = ""


def needs_setup(cfg: Config) -> bool:
    return not cfg.soulseek_username or not cfg.soulseek_password


def load_config() -> Config:
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return Config(
            soulseek_username=d.get("soulseek_username", ""),
            soulseek_password=_decrypt(d.get("soulseek_password_enc", "")),
            download_folder=d.get("download_folder", Config().download_folder),
            accepted_formats=d.get("accepted_formats", ["mp3", "flac", "wav"]),
            min_quality_kbps=int(d.get("min_quality_kbps", DEFAULT_MIN_QUALITY_KBPS)),
            max_workers=int(d.get("max_workers", DEFAULT_MAX_WORKERS)),
            retry_delay_minutes=int(d.get("retry_delay_minutes", DEFAULT_RETRY_DELAY_MINUTES)),
            bpm_threshold=float(d.get("bpm_threshold", DEFAULT_BPM_THRESHOLD)),
            port=int(d.get("port", DEFAULT_PORT)),
            autostart=bool(d.get("autostart", True)),
            analyzer_cloud_url=d.get("analyzer_cloud_url", ""),
            analyzer_cloud_key=d.get("analyzer_cloud_key", ""),
        )
    except (FileNotFoundError, json.JSONDecodeError):
        return Config()


def save_config(cfg: Config) -> None:
    data = {
        "soulseek_username": cfg.soulseek_username,
        "soulseek_password_enc": _encrypt(cfg.soulseek_password),
        "download_folder": cfg.download_folder,
        "accepted_formats": cfg.accepted_formats,
        "min_quality_kbps": cfg.min_quality_kbps,
        "max_workers": cfg.max_workers,
        "retry_delay_minutes": cfg.retry_delay_minutes,
        "bpm_threshold": cfg.bpm_threshold,
        "port": cfg.port,
        "autostart": cfg.autostart,
        "analyzer_cloud_url": cfg.analyzer_cloud_url,
        "analyzer_cloud_key": cfg.analyzer_cloud_key,
    }
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def set_autostart(enabled: bool) -> None:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{_exe_path()}"')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except OSError:
        pass


def is_autostart_enabled() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False
