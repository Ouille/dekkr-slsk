"""
Fenêtres tkinter de dekkr-slsk.

Architecture : un seul tk.Tk() caché tourne sur un thread dédié.
Toutes les fenêtres sont des tk.Toplevel() ouvertes via _tk_post().
Cela évite les crashs silencieux quand pystray appelle depuis son propre thread Win32.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable, Optional

from config import Config, save_config

APP_TITLE = "dekkr-slsk"
BG     = "#1a1a1a"
FG     = "#e0e0e0"
ACCENT = "#22c55e"
BTN_BG = "#2a2a2a"

# ── Thread tkinter dédié ───────────────────────────────────────────────────────

_tk_queue: queue.Queue = queue.Queue()
_tk_root:  Optional[tk.Tk] = None


def _tk_worker() -> None:
    global _tk_root
    _tk_root = tk.Tk()
    _tk_root.withdraw()   # fenêtre racine invisible
    _tk_root.title(APP_TITLE)

    def _poll():
        try:
            while True:
                fn = _tk_queue.get_nowait()
                fn(_tk_root)
        except queue.Empty:
            pass
        _tk_root.after(100, _poll)

    _poll()
    _tk_root.mainloop()


def start_tk_thread() -> None:
    """Appeler une fois au démarrage (depuis main.py)."""
    t = threading.Thread(target=_tk_worker, daemon=True, name="tk-thread")
    t.start()


def _tk_post(fn: Callable) -> None:
    """Poster une fonction à exécuter dans le thread tkinter."""
    _tk_queue.put(fn)


# ── Helpers UI ─────────────────────────────────────────────────────────────────

def _labeled_entry(parent, label: str, var: tk.Variable, row: int, show: str = "") -> tk.Entry:
    tk.Label(parent, text=label, bg=BG, fg=FG, width=20, anchor="w").grid(
        row=row, column=0, sticky="w", padx=8, pady=3)
    e = tk.Entry(parent, textvariable=var, bg=BTN_BG, fg=FG,
                 insertbackground=FG, show=show, width=26)
    e.grid(row=row, column=1, sticky="w", padx=4, pady=3)
    return e


def _labeled_spinbox(parent, label: str, var: tk.Variable, row: int,
                     from_: float, to: float, increment: float = 1,
                     fmt: str = "") -> tk.Spinbox:
    tk.Label(parent, text=label, bg=BG, fg=FG, width=20, anchor="w").grid(
        row=row, column=0, sticky="w", padx=8, pady=3)
    kw = dict(textvariable=var, bg=BTN_BG, fg=FG,
              buttonbackground=BTN_BG, width=8,
              from_=from_, to=to, increment=increment)
    if fmt:
        kw["format"] = fmt
    s = tk.Spinbox(parent, **kw)
    s.grid(row=row, column=1, sticky="w", padx=4, pady=3)
    return s


# ── Fenêtre de configuration (premier lancement) ──────────────────────────────

def open_setup_window(cfg: Config, on_success: Callable[[Config], None]) -> None:
    """
    Ouvre la fenêtre de setup et bloque jusqu'à validation.
    Doit être appelée depuis un thread non-tkinter.
    """
    done = threading.Event()
    result = [cfg]

    def _build(root: tk.Tk) -> None:
        win = tk.Toplevel(root)
        win.title(f"{APP_TITLE} — Configuration")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.grab_set()
        win.focus_force()

        tk.Label(win, text="Configuration dekkr-slsk", bg=BG, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(pady=(18, 4))
        tk.Label(win, text="Compte Soulseek", bg=BG, fg="#888",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=20)

        grid = tk.Frame(win, bg=BG)
        grid.pack(fill="x", padx=20, pady=4)

        var_user = tk.StringVar(value=cfg.soulseek_username)
        var_pass = tk.StringVar(value=cfg.soulseek_password)
        _labeled_entry(grid, "Username :",    var_user, 0)
        _labeled_entry(grid, "Mot de passe :", var_pass, 1, show="*")

        tk.Label(win, text="Dossier de téléchargement", bg=BG, fg="#888",
                 font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(8, 0))

        f2 = tk.Frame(win, bg=BG)
        f2.pack(fill="x", padx=20, pady=4)
        var_folder = tk.StringVar(value=cfg.download_folder)
        tk.Entry(f2, textvariable=var_folder, bg=BTN_BG, fg=FG,
                 insertbackground=FG, width=30).pack(side="left")

        def browse():
            d = filedialog.askdirectory(parent=win,
                                        initialdir=var_folder.get() or os.path.expanduser("~"))
            if d:
                var_folder.set(d)

        tk.Button(f2, text="…", command=browse, bg=BTN_BG, fg=FG, width=3).pack(side="left", padx=4)

        var_err = tk.StringVar()
        tk.Label(win, textvariable=var_err, bg=BG, fg="#f44336",
                 font=("Segoe UI", 8)).pack()
        tk.Label(win, text="→ Créer un compte : soulseeknet.org", bg=BG, fg="#555",
                 font=("Segoe UI", 8)).pack(pady=(0, 4))

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
            result[0] = cfg
            win.destroy()
            done.set()
            on_success(cfg)

        tk.Button(win, text="Se connecter", command=on_connect,
                  bg=ACCENT, fg="#000", font=("Segoe UI", 10, "bold"),
                  padx=16, pady=6).pack(pady=14)

        def on_close():
            done.set()

        win.protocol("WM_DELETE_WINDOW", on_close)

    _tk_post(_build)
    done.wait()


# ── Fenêtre de statut ─────────────────────────────────────────────────────────

_status_win: Optional[tk.Toplevel] = None


def open_status_window(cfg: Config) -> None:
    def _build(root: tk.Tk) -> None:
        global _status_win
        if _status_win and _status_win.winfo_exists():
            _status_win.lift()
            _status_win.focus_force()
            return

        import queue_manager
        import wishlist

        win = tk.Toplevel(root)
        win.title(f"{APP_TITLE} — Statut")
        win.configure(bg=BG)
        win.geometry("560x380")
        _status_win = win

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        tab_active = tk.Frame(nb, bg=BG)
        nb.add(tab_active, text="En cours")
        cols_a = ("Track", "État", "Tentative")
        tree_a = ttk.Treeview(tab_active, columns=cols_a, show="headings", height=8)
        for c in cols_a:
            tree_a.heading(c, text=c)
        tree_a.column("Track",     width=280)
        tree_a.column("État",      width=120)
        tree_a.column("Tentative", width=80)
        tree_a.pack(fill="both", expand=True, padx=4, pady=4)

        tab_queue = tk.Frame(nb, bg=BG)
        nb.add(tab_queue, text="Wishlist")
        cols_q = ("Track", "Artiste", "Ajouté le", "Raison")
        tree_q = ttk.Treeview(tab_queue, columns=cols_q, show="headings", height=8,
                              selectmode="extended")
        for c in cols_q:
            tree_q.heading(c, text=c)
        tree_q.column("Track",     width=180)
        tree_q.column("Artiste",   width=120)
        tree_q.column("Ajouté le", width=110)
        tree_q.column("Raison",    width=130)
        tree_q.pack(fill="both", expand=True, padx=4, pady=4)

        # Boutons de gestion de la wishlist
        q_btns = tk.Frame(tab_queue, bg=BG)
        q_btns.pack(fill="x", padx=4, pady=(0, 4))

        def remove_selected():
            sel = tree_q.selection()
            if not sel:
                return
            for iid in sel:
                wishlist.remove(iid)
            win.after(80, refresh)

        def clear_all():
            if not tree_q.get_children():
                return
            if messagebox.askyesno(
                "Vider la wishlist",
                "Retirer tous les tracks de la wishlist ?", parent=win):
                wishlist.clear()
                win.after(80, refresh)

        tk.Button(q_btns, text="Retirer la sélection", command=remove_selected,
                  bg=BTN_BG, fg=FG).pack(side="left", padx=2)
        tk.Button(q_btns, text="Tout vider", command=clear_all,
                  bg=BTN_BG, fg="#ef5350").pack(side="left", padx=2)

        def refresh():
            if not win.winfo_exists():
                return
            for row in tree_a.get_children():
                tree_a.delete(row)
            for job in queue_manager.get_active_jobs():
                tree_a.insert("", "end", values=(
                    f"{job.artist} — {job.title}",
                    job.status.value,
                    f"{job.attempts}/5",
                ))
            for row in tree_q.get_children():
                tree_q.delete(row)
            for item in wishlist.items():
                added = item.added_at.strftime("%d/%m %H:%M") if item.added_at else "—"
                tree_q.insert("", "end", iid=item.query, values=(
                    item.title, item.artist, added, item.reason or "",
                ))
            win.after(2000, refresh)

        refresh()

    _tk_post(_build)


# ── Fenêtre Paramètres ────────────────────────────────────────────────────────

def open_settings_window(cfg: Config, on_save: Optional[Callable[[Config], None]] = None) -> None:
    def _build(root: tk.Tk) -> None:
        win = tk.Toplevel(root)
        win.title(f"{APP_TITLE} — Paramètres")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.focus_force()

        def section(text: str):
            tk.Label(win, text=text, bg=BG, fg=ACCENT,
                     font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))

        def grid_frame() -> tk.Frame:
            f = tk.Frame(win, bg=BG)
            f.pack(fill="x", padx=16, pady=2)
            return f

        # Compte Soulseek
        section("Compte Soulseek")
        g1 = grid_frame()
        var_user = tk.StringVar(value=cfg.soulseek_username)
        var_pass = tk.StringVar(value=cfg.soulseek_password)
        _labeled_entry(g1, "Username :",    var_user, 0)
        _labeled_entry(g1, "Mot de passe :", var_pass, 1, show="*")

        # Dossier
        section("Téléchargement")
        g2 = grid_frame()
        var_folder = tk.StringVar(value=cfg.download_folder)
        tk.Label(g2, text="Dossier :", bg=BG, fg=FG, width=20, anchor="w").grid(
            row=0, column=0, sticky="w", padx=8, pady=3)
        f_folder = tk.Frame(g2, bg=BG)
        f_folder.grid(row=0, column=1, sticky="w")
        tk.Entry(f_folder, textvariable=var_folder, bg=BTN_BG, fg=FG,
                 insertbackground=FG, width=22).pack(side="left")

        def browse():
            d = filedialog.askdirectory(parent=win,
                                        initialdir=var_folder.get() or os.path.expanduser("~"))
            if d:
                var_folder.set(d)

        tk.Button(f_folder, text="…", command=browse, bg=BTN_BG, fg=FG, width=3).pack(side="left", padx=4)

        # Formats
        section("Qualité & Formats")
        g3 = grid_frame()
        var_mp3  = tk.BooleanVar(value="mp3"  in cfg.accepted_formats)
        var_flac = tk.BooleanVar(value="flac" in cfg.accepted_formats)
        var_wav  = tk.BooleanVar(value="wav"  in cfg.accepted_formats)
        tk.Label(g3, text="Formats :", bg=BG, fg=FG, width=20, anchor="w").grid(
            row=0, column=0, sticky="w", padx=8)
        fmt_frame = tk.Frame(g3, bg=BG)
        fmt_frame.grid(row=0, column=1, sticky="w")
        for text, var in [("MP3", var_mp3), ("FLAC", var_flac), ("WAV", var_wav)]:
            tk.Checkbutton(fmt_frame, text=text, variable=var, bg=BG, fg=FG,
                           selectcolor=BTN_BG, activebackground=BG).pack(side="left", padx=4)

        g4 = grid_frame()
        var_kbps = tk.IntVar(value=cfg.min_quality_kbps)
        _labeled_spinbox(g4, "Qualité min (kbps) :", var_kbps, 0, 128, 1411, 32)

        # Comportement
        section("Comportement")
        g5 = grid_frame()
        var_workers = tk.IntVar(value=cfg.max_workers)
        var_retry   = tk.IntVar(value=cfg.retry_delay_minutes)
        var_bpm     = tk.DoubleVar(value=cfg.bpm_threshold)
        var_sdelay  = tk.DoubleVar(value=getattr(cfg, "search_delay_seconds", 3.0))
        _labeled_spinbox(g5, "Workers simultanés :", var_workers, 0, 1, 3)
        _labeled_spinbox(g5, "Délai retry (min) :",  var_retry,   1, 5, 120)
        _labeled_spinbox(g5, "Seuil BPM (±) :",      var_bpm,     2, 0.5, 10.0, 0.5, "%.1f")
        _labeled_spinbox(g5, "Délai entre recherches (s) :", var_sdelay, 3, 0.0, 30.0, 0.5, "%.1f")

        # Cloud analyzer (optionnel)
        section("Analyzer cloud (optionnel)")
        g6 = grid_frame()
        var_cloud_url = tk.StringVar(value=cfg.analyzer_cloud_url)
        var_cloud_key = tk.StringVar(value=cfg.analyzer_cloud_key)
        _labeled_entry(g6, "URL :",     var_cloud_url, 0)
        _labeled_entry(g6, "Clé API :", var_cloud_key, 1, show="*")

        def save():
            fmts = [f for f, v in [("mp3", var_mp3), ("flac", var_flac), ("wav", var_wav)] if v.get()]
            if not fmts:
                messagebox.showerror("Erreur", "Sélectionnez au moins un format.", parent=win)
                return
            cfg.soulseek_username   = var_user.get().strip()
            cfg.soulseek_password   = var_pass.get().strip()
            cfg.download_folder     = var_folder.get().strip()
            cfg.accepted_formats    = fmts
            cfg.min_quality_kbps    = var_kbps.get()
            cfg.max_workers         = var_workers.get()
            cfg.retry_delay_minutes = var_retry.get()
            cfg.bpm_threshold       = round(var_bpm.get(), 1)
            cfg.search_delay_seconds = round(var_sdelay.get(), 1)
            cfg.analyzer_cloud_url  = var_cloud_url.get().strip()
            cfg.analyzer_cloud_key  = var_cloud_key.get().strip()
            save_config(cfg)
            # Application immédiate du délai de recherche (sans redémarrage)
            try:
                import searcher
                searcher.set_search_delay(cfg.search_delay_seconds)
            except Exception:
                pass
            if on_save:
                on_save(cfg)
            win.destroy()

        tk.Button(win, text="Enregistrer", command=save,
                  bg=ACCENT, fg="#000", font=("Segoe UI", 10, "bold"),
                  padx=16, pady=6).pack(pady=14)

    _tk_post(_build)
