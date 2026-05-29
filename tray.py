import threading
import pystray
from PIL import Image, ImageDraw

APP_NAME = "dekkr-slsk"

_COLORS = {
    "idle":         (34,  197, 94),   # vert
    "active":       (250, 204, 21),   # jaune/or — jobs en cours
    "queued":       (34,  197, 94),   # vert + badge textuel dans le menu
    "disconnected": (249, 115, 22),   # orange
    "error":        (239,  68, 68),   # rouge
    "stopped":      (107, 114, 128),  # gris
}


def _make_icon(color: tuple, badge: int = 0) -> Image.Image:
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=(*color, 255))
    if badge > 0:
        # Petit cercle blanc en haut à droite avec le chiffre
        draw.ellipse([38, 4, 60, 26], fill=(255, 255, 255, 255))
        draw.text((49, 15), str(min(badge, 99)), fill=(0, 0, 0, 255), anchor="mm")
    return img


class TrayIcon:
    def __init__(self, config, on_open_status, on_open_settings, on_restart, on_quit):
        self._cfg              = config
        self._on_open_status   = on_open_status
        self._on_open_settings = on_open_settings
        self._on_restart       = on_restart
        self._on_quit          = on_quit
        self._state            = "idle"
        self._active           = 0
        self._waiting          = 0
        self._icon: pystray.Icon | None = None

    def _build_menu(self) -> pystray.Menu:
        from config import is_autostart_enabled, set_autostart, save_config

        status_label = {
            "idle":         f"Connecté — port {self._cfg.port}",
            "active":       f"Connecté — {self._active} téléchargement(s) actif(s)",
            "queued":       f"Connecté — {self._waiting} track(s) en attente",
            "disconnected": "Déconnecté du réseau Soulseek",
            "error":        "Erreur — voir les paramètres",
            "stopped":      "Arrêté",
        }.get(self._state, "dekkr-slsk")

        def toggle_autostart(icon, item):
            new_val = not is_autostart_enabled()
            set_autostart(new_val)
            self._cfg.autostart = new_val
            save_config(self._cfg)
            self._refresh_menu()

        items = [
            pystray.MenuItem(status_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Ouvrir la fenêtre de statut",    lambda i, it: self._on_open_status()),
            pystray.MenuItem("Paramètres",                     lambda i, it: self._on_open_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Démarrer au démarrage Windows",
                toggle_autostart,
                checked=lambda item: is_autostart_enabled(),
            ),
            pystray.MenuItem("Redémarrer", lambda i, it: self._on_restart()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quitter", lambda i, it: self._do_quit()),
        ]
        return pystray.Menu(*items)

    def _refresh_menu(self) -> None:
        if self._icon:
            self._icon.menu = self._build_menu()
            self._icon.update_menu()

    def update_state(self, active: int, waiting: int, connected: bool) -> None:
        self._active  = active
        self._waiting = waiting
        if not connected:
            self._state = "disconnected"
        elif active > 0:
            self._state = "active"
        elif waiting > 0:
            self._state = "queued"
        else:
            self._state = "idle"
        if self._icon:
            color = _COLORS.get(self._state, _COLORS["idle"])
            self._icon.icon = _make_icon(color, waiting)
            self._icon.title = f"{APP_NAME} — {self._state}"
            self._refresh_menu()

    def set_error(self) -> None:
        self._state = "error"
        if self._icon:
            self._icon.icon = _make_icon(_COLORS["error"])
            self._refresh_menu()

    def notify(self, msg: str) -> None:
        if self._icon:
            try:
                self._icon.notify(msg, APP_NAME)
            except Exception:
                pass

    def _do_quit(self) -> None:
        if self._icon:
            self._icon.stop()
        self._on_quit()

    def run(self) -> None:
        self._icon = pystray.Icon(
            APP_NAME,
            icon=_make_icon(_COLORS["idle"]),
            title=APP_NAME,
            menu=self._build_menu(),
        )
        self._icon.run()
