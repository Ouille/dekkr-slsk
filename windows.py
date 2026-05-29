"""
Fenêtres tkinter de dekkr-slsk :
  - setup_window()    : configuration premier lancement
  - status_window()   : jobs en cours + liste d'attente
  - settings_window() : modification de la configuration
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable, Optional

from config import Config, save_config, needs_setup

APP_TITLE = "dekkr-slsk"
BG = "#1a1a1a"
FG = "#e0e0e0"
ACCENT = "#22c55e"
BTN_BG = "#2a2a2a"


def _style_widget(w, bg=BG, fg=FG):
    try:
        w.configure(bg=bg, fg=fg)
    except tk.TclError:
        pass


# ── Fenêtre de configuration (premier lancement) ──────────────────────────────

def open_setup_window(cfg: Config, on_success: Callable[[Config], None]) -> None:
    """Bloque jusqu'à validation des credentials."""
    root = tk.Tk()
    root.title(f"{APP_TITLE} — Configuration")
    root.configure(bg=BG)
    root.resizable(False, False)

    tk.Label(root, text="Configuration dekkr-slsk", bg=BG, fg=ACCENT,
             font=("Segoe UI", 13, "bold")).pack(pady=(18, 4))
    tk.Label(root, text="Compte Soulseek", bg=BG, fg="#888",
             font=("Segoe UI", 9)).pack(anchor="w", padx=20)

    frame = tk.Frame(root, bg=BG)
    frame.pack(fill="x", padx=20, pady=4)

    tk.Label(frame, text="Username :", bg=BG, fg=FG, width=12, anchor="w").grid(row=0, column=0, sticky="w")
    var_user = tk.StringVar(value=cfg.soulseek_username)
    tk.Entry(frame, textvariable=var_user, bg=BTN_BG, fg=FG, insertbackground=FG, width=28).grid(row=0, column=1)

    tk.Label(frame, text="Mot de passe :", bg=BG, fg=FG, width=12, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
    var_pass = tk.StringVar(value=cfg.soulseek_password)
    tk.Entry(frame, textvariable=var_pass, show="*", bg=BTN_BG, fg=FG, insertbackground=FG, width=28).grid(row=1, column=1)

    tk.Label(root, text="Dossier de téléchargement", bg=BG, fg="#888",
             font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(8, 0))

    frame2 = tk.Frame(root, bg=BG)
    frame2.pack(fill="x", padx=20, pady=4)
    var_folder = tk.StringVar(value=cfg.download_folder)
    tk.Entry(frame2, textvariable=var_folder, bg=BTN_BG, fg=FG, insertbackground=FG, width=30).pack(side="left")

    def browse():
        d = filedialog.askdirectory(initialdir=var_folder.get() or os.path.expanduser("~"))
        if d:
            var_folder.set(d)

    tk.Button(frame2, text="…", command=browse, bg=BTN_BG, fg=FG, width=3).pack(side="left", padx=4)

    var_err = tk.StringVar()
    lbl_err = tk.Label(root, textvariable=var_err, bg=BG, fg="#f44336", font=("Segoe UI", 8))
    lbl_err.pack()

    tk.Label(root, text="→ Créer un compte : soulseeknet.org", bg=BG, fg="#555",
             font=("Segoe UI", 8), cursor="hand2").pack(pady=(0, 4))

    def on_connect():
        u = var_user.get().strip()
        p = var_pass.get().strip()
        f = var_folder.get().strip()
        if not u or not p:
            var_err.set("Username et mot de passe requis.")
            return
        if not f:
            var_err.set("Choisissez un dossier de téléchargement.")
            return
        cfg.soulseek_username = u
        cfg.soulseek_password = p
        cfg.download_folder   = f
        save_config(cfg)
        root.destroy()
        on_success(cfg)

    tk.Button(root, text="Se connecter", command=on_connect,
              bg=ACCENT, fg="#000", font=("Segoe UI", 10, "bold"),
              padx=16, pady=6).pack(pady=14)

    root.mainloop()


# ── Fenêtre de statut ─────────────────────────────────────────────────────────

_status_window: Optional[tk.Toplevel] = None
_status_lock = threading.Lock()


def open_status_window(cfg: Config) -> None:
    import queue_manager
    from queue_manager import JobStatus

    global _status_window
    with _status_lock:
        if _status_window and tk.Toplevel.winfo_exists(_status_window):
            _status_window.lift()
            return

    def _build():
        global _status_window
        root = tk.Tk()
        root.title(f"{APP_TITLE} — Statut")
        root.configure(bg=BG)
        root.geometry("560x400")
        _status_window = root

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        # Tab En cours
        tab_active = tk.Frame(nb, bg=BG)
        nb.add(tab_active, text="En cours")
        cols_a = ("Track", "État", "Tentative")
        tree_a = ttk.Treeview(tab_active, columns=cols_a, show="headings", height=8)
        for c in cols_a:
            tree_a.heading(c, text=c)
        tree_a.column("Track", width=280)
        tree_a.column("État",  width=120)
        tree_a.column("Tentative", width=80)
        tree_a.pack(fill="both", expand=True, padx=4, pady=4)

        # Tab Liste d'attente
        tab_queue = tk.Frame(nb, bg=BG)
        nb.add(tab_queue, text="Liste d'attente")
        cols_q = ("Track", "Artiste", "Prochain retry", "Raison")
        tree_q = ttk.Treeview(tab_queue, columns=cols_q, show="headings", height=8)
        for c in cols_q:
            tree_q.heading(c, text=c)
        tree_q.column("Track",         width=180)
        tree_q.column("Artiste",       width=120)
        tree_q.column("Prochain retry", width=90)
        tree_q.column("Raison",        width=140)
        tree_q.pack(fill="both", expand=True, padx=4, pady=4)

        def refresh():
            for row in tree_a.get_children():
                tree_a.delete(row)
            for job in queue_manager.get_active_jobs():
                tree_a.insert("", "end", values=(
                    f"{job.artist} — {job.title}",
                    job.status.value,
                    f"{job.attempts}/{5}",
                ))
            for row in tree_q.get_children():
                tree_q.delete(row)
            for job in queue_manager.get_queued_jobs():
                retry = job.retry_at.strftime("%H:%M:%S") if job.retry_at else "—"
                tree_q.insert("", "end", values=(
                    job.title,
                    job.artist,
                    retry,
                    job.error or "",
                ))
            root.after(2000, refresh)

        refresh()
        root.mainloop()

    threading.Thread(target=_build, daemon=True).start()


# ── Fenêtre Paramètres ────────────────────────────────────────────────────────

def open_settings_window(cfg: Config, on_save: Optional[Callable[[Config], None]] = None) -> None:
    def _build():
        root = tk.Tk()
        root.title(f"{APP_TITLE} — Paramètres")
        root.configure(bg=BG)
        root.resizable(False, False)

        def row(parent, label, widget_fn, **kwargs):
            f = tk.Frame(parent, bg=BG)
            f.pack(fill="x", padx=16, pady=3)
            tk.Label(f, text=label, bg=BG, fg=FG, width=22, anchor="w").pack(side="left")
            return widget_fn(f, **kwargs)

        # Compte Soulseek
        tk.Label(root, text="Compte Soulseek", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        var_user = tk.StringVar(value=cfg.soulseek_username)
        row(root, "Username :", lambda p, **kw: tk.Entry(p, textvariable=var_user, bg=BTN_BG, fg=FG, insertbackground=FG, width=24))

        var_pass = tk.StringVar(value=cfg.soulseek_password)
        row(root, "Mot de passe :", lambda p, **kw: tk.Entry(p, textvariable=var_pass, show="*", bg=BTN_BG, fg=FG, insertbackground=FG, width=24))

        # Dossier
        tk.Label(root, text="Téléchargement", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        var_folder = tk.StringVar(value=cfg.download_folder)
        f_folder = tk.Frame(root, bg=BG)
        f_folder.pack(fill="x", padx=16, pady=3)
        tk.Label(f_folder, text="Dossier :", bg=BG, fg=FG, width=22, anchor="w").pack(side="left")
        tk.Entry(f_folder, textvariable=var_folder, bg=BTN_BG, fg=FG, insertbackground=FG, width=22).pack(side="left")

        def browse():
            d = filedialog.askdirectory(initialdir=var_folder.get() or os.path.expanduser("~"))
            if d:
                var_folder.set(d)
        tk.Button(f_folder, text="…", command=browse, bg=BTN_BG, fg=FG, width=3).pack(side="left", padx=4)

        # Formats
        tk.Label(root, text="Qualité & Formats", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))

        f_fmt = tk.Frame(root, bg=BG)
        f_fmt.pack(anchor="w", padx=16, pady=3)
        tk.Label(f_fmt, text="Formats acceptés :", bg=BG, fg=FG, width=22, anchor="w").pack(side="left")
        var_mp3  = tk.BooleanVar(value="mp3"  in cfg.accepted_formats)
        var_flac = tk.BooleanVar(value="flac" in cfg.accepted_formats)
        var_wav  = tk.BooleanVar(value="wav"  in cfg.accepted_formats)
        tk.Checkbutton(f_fmt, text="MP3",  variable=var_mp3,  bg=BG, fg=FG, selectcolor=BTN_BG, activebackground=BG).pack(side="left")
        tk.Checkbutton(f_fmt, text="FLAC", variable=var_flac, bg=BG, fg=FG, selectcolor=BTN_BG, activebackground=BG).pack(side="left")
        tk.Checkbutton(f_fmt, text="WAV",  variable=var_wav,  bg=BG, fg=FG, selectcolor=BTN_BG, activebackground=BG).pack(side="left")

        var_kbps = tk.IntVar(value=cfg.min_quality_kbps)
        f_kbps = row(root, "Qualité min (kbps) :", lambda p, **kw: tk.Spinbox(p, from_=128, to=1411, increment=32, textvariable=var_kbps, bg=BTN_BG, fg=FG, width=8, buttonbackground=BTN_BG))

        # Workers & retry
        tk.Label(root, text="Comportement", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))

        var_workers = tk.IntVar(value=cfg.max_workers)
        row(root, "Workers simultanés :", lambda p, **kw: tk.Spinbox(p, from_=1, to=3, textvariable=var_workers, bg=BTN_BG, fg=FG, width=4, buttonbackground=BTN_BG))

        var_retry = tk.IntVar(value=cfg.retry_delay_minutes)
        row(root, "Délai retry (min) :", lambda p, **kw: tk.Spinbox(p, from_=5, to=120, textvariable=var_retry, bg=BTN_BG, fg=FG, width=4, buttonbackground=BTN_BG))

        var_bpm = tk.DoubleVar(value=cfg.bpm_threshold)
        row(root, "Seuil BPM (±) :", lambda p, **kw: tk.Spinbox(p, from_=0.5, to=10.0, increment=0.5, textvariable=var_bpm, bg=BTN_BG, fg=FG, width=6, buttonbackground=BTN_BG, format="%.1f"))

        # Cloud analyzer (optionnel)
        tk.Label(root, text="Analyzer cloud (optionnel)", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        var_cloud_url = tk.StringVar(value=cfg.analyzer_cloud_url)
        row(root, "URL :", lambda p, **kw: tk.Entry(p, textvariable=var_cloud_url, bg=BTN_BG, fg=FG, insertbackground=FG, width=28))
        var_cloud_key = tk.StringVar(value=cfg.analyzer_cloud_key)
        row(root, "Clé API :", lambda p, **kw: tk.Entry(p, textvariable=var_cloud_key, show="*", bg=BTN_BG, fg=FG, insertbackground=FG, width=28))

        def save():
            fmts = []
            if var_mp3.get():  fmts.append("mp3")
            if var_flac.get(): fmts.append("flac")
            if var_wav.get():  fmts.append("wav")
            if not fmts:
                messagebox.showerror("Erreur", "Sélectionnez au moins un format.")
                return
            cfg.soulseek_username  = var_user.get().strip()
            cfg.soulseek_password  = var_pass.get().strip()
            cfg.download_folder    = var_folder.get().strip()
            cfg.accepted_formats   = fmts
            cfg.min_quality_kbps   = var_kbps.get()
            cfg.max_workers        = var_workers.get()
            cfg.retry_delay_minutes = var_retry.get()
            cfg.bpm_threshold      = round(var_bpm.get(), 1)
            cfg.analyzer_cloud_url = var_cloud_url.get().strip()
            cfg.analyzer_cloud_key = var_cloud_key.get().strip()
            save_config(cfg)
            if on_save:
                on_save(cfg)
            root.destroy()

        tk.Button(root, text="Enregistrer", command=save,
                  bg=ACCENT, fg="#000", font=("Segoe UI", 10, "bold"),
                  padx=16, pady=6).pack(pady=14)

        root.mainloop()

    threading.Thread(target=_build, daemon=True).start()
