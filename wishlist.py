"""
Wishlist Soulseek (recherche différée) — SPEC-SLSK-003 / ADR-020.

Quand une recherche active échoue (aucun candidat / tous downloads échoués),
le track est inscrit dans la wishlist native d'aioslsk
(`client.settings.searches.wishlist`). aioslsk relance les requêtes tout seul à
l'intervalle imposé par le serveur (~600 s). Les résultats arrivent en
évènements (`SearchResultEvent` type WISHLIST) → on score, on télécharge le
meilleur après une courte fenêtre de collecte, puis ON RETIRE L'ITEM
(impératif anti BUG-006).

Persistance : %APPDATA%\\dekkr-slsk\\wishlist.json (items en attente uniquement —
les items satisfaits sont retirés, donc jamais ré-téléchargés au redémarrage).
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from aioslsk.settings import WishlistSettingEntry

import downloader
import history
import searcher
import textutil
from verifier import read_metadata

APP_NAME = "dekkr-slsk"


@dataclass
class WishItem:
    artist: str
    title: str
    bpm: Optional[float]
    key: Optional[str]
    duration: Optional[float]
    reason: str            # "aucun_candidat" | "echecs_download"
    query: str             # textutil.clean_for_search — clé de rattachement des résultats
    added_at: datetime = field(default_factory=datetime.now)
    # état runtime (non persisté)
    collecting: bool = False
    done: bool = False
    candidates: list = field(default_factory=list)


_items: dict[str, WishItem] = {}        # clé = query nettoyée
_tasks: set = set()
_client = None
_cfg = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_notify_cb = None                        # callable(msg) pour les toasts tray


# ── Persistance ─────────────────────────────────────────────────────────────────

def _path() -> str:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    folder = os.path.join(appdata, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "wishlist.json")


def _persist() -> None:
    data = [
        {
            "artist": it.artist, "title": it.title, "bpm": it.bpm, "key": it.key,
            "duration": it.duration, "reason": it.reason, "query": it.query,
            "added_at": it.added_at.isoformat(),
        }
        for it in _items.values()
    ]
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _load() -> None:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    for d in data:
        try:
            added = datetime.fromisoformat(d.get("added_at")) if d.get("added_at") else datetime.now()
        except (ValueError, TypeError):
            added = datetime.now()
        item = WishItem(
            artist=d.get("artist", ""), title=d.get("title", ""),
            bpm=d.get("bpm"), key=d.get("key"), duration=d.get("duration"),
            reason=d.get("reason", ""), query=d.get("query", ""), added_at=added,
        )
        if item.query:
            _items[item.query] = item


# ── Init / câblage ──────────────────────────────────────────────────────────────

def init(client, cfg, notify_cb=None) -> None:
    """Charge la persistance et réinjecte les items dans la wishlist aioslsk."""
    global _client, _cfg, _loop, _notify_cb
    _client = client
    _cfg = cfg
    _notify_cb = notify_cb
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None

    _load()
    entries = [WishlistSettingEntry(query=it.query) for it in _items.values()]
    if entries:
        client.settings.searches.wishlist = list(client.settings.searches.wishlist) + entries


def _accepted() -> set:
    return {f.lower() for f in (_cfg.accepted_formats if _cfg else [])}


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _emit_change() -> None:
    """Met à jour le badge tray (compte wishlist) via queue_manager."""
    try:
        import queue_manager
        queue_manager.notify_state()
    except Exception:
        pass


def _notify_tray(msg: str) -> None:
    if _notify_cb:
        try:
            _notify_cb(msg)
        except Exception:
            pass


# ── API publique ────────────────────────────────────────────────────────────────

def count() -> int:
    return len(_items)


def items() -> list[WishItem]:
    return list(_items.values())


def add(job, reason: str) -> bool:
    """
    Ajoute un job à la wishlist. Retourne True si ajouté.
    - si le fichier existe déjà sur le disque → journal 'deja_present', pas d'ajout
    - si doublon (même query) → pas d'ajout
    Appelé depuis la boucle asyncio (queue_manager._run).
    """
    query = textutil.clean_for_search(getattr(job, "artist", ""), getattr(job, "title", ""))
    if not query:
        return False

    existing = downloader.already_present(
        _cfg.download_folder, job.artist, job.title, _cfg.accepted_formats
    )
    if existing:
        history.log(_cfg, job, "deja_present", local_path=existing,
                    verification="fichier deja present")
        return False

    if query in _items:
        return False  # doublon

    item = WishItem(
        artist=job.artist, title=job.title,
        bpm=getattr(job, "bpm", None), key=getattr(job, "key", None),
        duration=getattr(job, "duration", None), reason=reason, query=query,
    )
    _items[query] = item
    _client.settings.searches.wishlist = list(_client.settings.searches.wishlist) + [
        WishlistSettingEntry(query=query)
    ]
    _persist()
    history.log(_cfg, job, "ajout_wishlist", verification=reason)
    _emit_change()
    return True


def _remove_impl(query: str) -> None:
    _items.pop(query, None)
    if _client is not None:
        _client.settings.searches.wishlist = [
            e for e in _client.settings.searches.wishlist if e.query != query
        ]
    _persist()
    _emit_change()


def _clear_impl() -> None:
    _items.clear()
    if _client is not None:
        _client.settings.searches.wishlist = []
    _persist()
    _emit_change()


def remove(query: str) -> None:
    """Retrait d'un item (thread-safe, appelable depuis tkinter)."""
    if _loop and _loop.is_running():
        _loop.call_soon_threadsafe(_remove_impl, query)
    else:
        _remove_impl(query)


def clear() -> None:
    """Vide toute la wishlist (thread-safe, appelable depuis tkinter)."""
    if _loop and _loop.is_running():
        _loop.call_soon_threadsafe(_clear_impl)
    else:
        _clear_impl()


# ── Réception des résultats wishlist ────────────────────────────────────────────

async def on_search_result(event) -> None:
    """Handler SearchResultEvent — ne traite que les résultats de type WISHLIST."""
    try:
        from aioslsk.search.model import SearchType
    except Exception:
        SearchType = None

    req = getattr(event, "query", None)
    if req is None:
        return
    if SearchType is not None and getattr(req, "search_type", None) != SearchType.WISHLIST:
        return

    query = getattr(req, "query", None)
    item = _items.get(query) if query else None
    if item is None or item.done:
        return

    cands = searcher.candidates_from_result(
        event.result, item.duration or 0, _accepted(), _cfg.min_quality_kbps
    )
    if not cands:
        return

    item.candidates.extend(cands)
    if not item.collecting:
        item.collecting = True
        _spawn(_collect_then_download(item))


async def _collect_then_download(item: WishItem) -> None:
    """Fenêtre de collecte courte puis téléchargement du meilleur candidat."""
    try:
        await asyncio.sleep(_cfg.wishlist_collect_seconds)

        # Le fichier a pu apparaître entre-temps (anti re-téléchargement)
        existing = downloader.already_present(
            _cfg.download_folder, item.artist, item.title, _cfg.accepted_formats
        )
        if existing:
            history.log(_cfg, item, "deja_present", local_path=existing,
                        verification="fichier deja present")
            _finish(item)
            return

        ordered = sorted(item.candidates, key=lambda c: c.score, reverse=True)
        for candidate in ordered[:searcher.MAX_CANDIDATES]:
            try:
                file_path = await downloader.download(_client, candidate)
            except Exception:
                continue
            file_path = downloader.rename_to(file_path, item.artist, item.title)
            meta = read_metadata(file_path)
            history.log(_cfg, item, "wishlist_trouve", candidate=candidate, meta=meta,
                        local_path=file_path, verification="OK")
            _finish(item)
            _notify_tray(f"{item.artist} - {item.title} trouvé via wishlist")
            return

        # Tous les candidats de ce cycle ont échoué → on retentera au prochain
        # cycle serveur (l'item reste dans la wishlist).
        item.candidates.clear()
        item.collecting = False
    except asyncio.CancelledError:
        item.collecting = False


def _finish(item: WishItem) -> None:
    """Marque l'item satisfait et le retire (anti BUG-006)."""
    item.done = True
    _remove_impl(item.query)
