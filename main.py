"""
dekkr-slsk — point d'entrée.

Architecture :
  - Thread daemon : asyncio event loop (uvicorn + workers Soulseek)
  - Thread principal : pystray (bloquant)
  - Threads supplémentaires : fenêtres tkinter (chacune dans son thread)
"""

import asyncio
import os
import sys
import threading
import time

import uvicorn

from config import load_config, save_config, needs_setup, set_autostart
import queue_manager
import server as _server
import slsk_session
from tray import TrayIcon
from windows import open_setup_window, open_status_window, open_settings_window, start_tk_thread

VERSION = "1.0.2"


def _start_server(cfg, loop: asyncio.AbstractEventLoop) -> uvicorn.Server:
    uv_config = uvicorn.Config(
        app       = _server.app,
        host      = "127.0.0.1",
        port      = cfg.port,
        log_level = "warning",
        log_config= None,
        loop      = "none",  # on fournit notre propre loop
    )
    uv_server = uvicorn.Server(uv_config)
    loop.create_task(uv_server.serve())
    return uv_server


async def _async_main(cfg, tray_ref: list) -> None:
    """Boucle asyncio principale : connexion Soulseek + traitement des jobs."""
    # Connexion Soulseek
    try:
        client = await slsk_session.connect(cfg.soulseek_username, cfg.soulseek_password, cfg.download_folder)
        _server.set_connected(True)
    except Exception as e:
        _server.set_connected(False)
        if tray_ref and tray_ref[0]:
            tray_ref[0].set_error()
            tray_ref[0].notify(f"Connexion Soulseek échouée : {e}")
        # Attendre une reconnexion manuelle (restart)
        await asyncio.sleep(999_999)
        return

    # Stocke client + config : chaque job lancera sa propre tâche (parallélisme illimité)
    queue_manager.init(client, cfg)

    # Wishlist (recherche différée) : charge la persistance, réinjecte dans aioslsk,
    # et s'abonne aux résultats de recherche.
    import wishlist
    wishlist.init(
        client, cfg,
        notify_cb=lambda msg: tray_ref[0].notify(msg) if tray_ref and tray_ref[0] else None,
    )
    slsk_session.register_result_handler(wishlist.on_search_result)

    # Callback état : mise à jour du tray
    def on_state(active: int, waiting: int) -> None:
        if tray_ref and tray_ref[0]:
            tray_ref[0].update_state(active, waiting, connected=slsk_session.is_connected())

    queue_manager.register_state_callback(on_state)

    # Notification cloud si utilisée
    def on_job_created(job) -> None:
        pass  # hook futur

    _server.register_job_callback(on_job_created)

    # Garder la boucle vivante (les jobs tournent dans leurs propres tâches)
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass


def main() -> None:
    cfg = load_config()

    # Démarrer le thread tkinter dédié (doit précéder toute ouverture de fenêtre)
    start_tk_thread()

    if cfg.autostart:
        set_autostart(True)

    tray_ref: list = [None]

    # Si pas de config → fenêtre setup (bloquante, dans le thread principal temporairement)
    if needs_setup(cfg):
        setup_done = threading.Event()
        setup_result = [cfg]

        def on_setup_success(new_cfg):
            setup_result[0] = new_cfg
            setup_done.set()

        # Ouvrir setup dans un thread pour ne pas bloquer pystray
        t = threading.Thread(target=lambda: open_setup_window(cfg, on_setup_success), daemon=True)
        t.start()
        setup_done.wait()
        cfg = setup_result[0]

    # Exposer le dossier de téléchargement via /health (consommé par DekkR — SPEC-SLSK-004)
    _server.set_download_folder(cfg.download_folder)

    # Démarrer la boucle asyncio dans un thread daemon
    loop = asyncio.new_event_loop()

    def start_loop():
        asyncio.set_event_loop(loop)
        try:
            _start_server(cfg, loop)
        except OSError as e:
            if tray_ref[0]:
                tray_ref[0].set_error()
                tray_ref[0].notify(f"Port {cfg.port} déjà utilisé")
            return

        loop.run_until_complete(_async_main(cfg, tray_ref))

    loop_thread = threading.Thread(target=start_loop, daemon=True, name="asyncio-loop")
    loop_thread.start()

    # Petite pause pour laisser le serveur démarrer
    time.sleep(0.5)

    # Tray (bloquant — thread principal)
    def on_restart():
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def on_quit():
        loop.call_soon_threadsafe(loop.stop)
        os._exit(0)

    tray = TrayIcon(
        config           = cfg,
        on_open_status   = lambda: open_status_window(cfg),
        on_open_settings = lambda: open_settings_window(cfg, on_save=lambda c: save_config(c)),
        on_restart       = on_restart,
        on_quit          = on_quit,
    )
    tray_ref[0] = tray

    tray.notify(f"dekkr-slsk connecté au réseau Soulseek — port {cfg.port}")
    tray.run()  # Bloque le thread principal


if __name__ == "__main__":
    main()
