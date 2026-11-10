"""
Recherche et scoring de résultats sur le réseau Soulseek via aioslsk.

API aioslsk 1.6.x (vérifiée sur la vraie structure) :
  request.results         : list[SearchResult]
  result.username         : str
  result.has_free_slots   : bool
  result.shared_items     : list[FileData]
  item.filename           : str (chemin Windows, séparateur backslash)
  item.filesize           : int (octets)
  item.attributes         : list[Attribute(key, value)]
    key 0 = bitrate kbps (absent pour FLAC)
    key 1 = durée secondes
    key 4 = sample rate Hz
    key 5 = bit depth
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

import textutil

SEARCH_TIMEOUT_S = 15
MAX_CANDIDATES   = 5

# ── Anti-flood ──────────────────────────────────────────────────────────────────
# Soulseek limite le débit de recherches par compte/IP. Sans espacement, lancer
# beaucoup de tracks d'un coup (SLSK ALL) fait ignorer TOUTES les recherches
# (côté dekkr-slsk ET côté client Soulseek normal). On espace donc le DÉPART de
# chaque recherche ; les téléchargements restent parallèles.
_search_delay = 3.0
_search_gate = asyncio.Lock()
_last_search_at = 0.0


def set_search_delay(seconds: float) -> None:
    global _search_delay
    _search_delay = max(0.0, float(seconds or 0))


async def _throttle() -> None:
    """Garantit au moins `_search_delay` secondes entre deux départs de recherche."""
    global _last_search_at
    async with _search_gate:
        loop = asyncio.get_event_loop()
        wait = _search_delay - (loop.time() - _last_search_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_search_at = loop.time()

_FORMAT_SCORE = {"flac": 300, "mp3": 200, "wav": 100}


@dataclass
class SearchCandidate:
    username:    str
    remote_path: str
    filename:    str
    fmt:         str    # extension normalisée ('mp3', 'flac', ...)
    size:        int
    bitrate:     int    # 0 si inconnu/lossless
    duration:    float
    free_slot:   bool
    score:       float = 0.0


def _extract_attributes(item) -> tuple[int, float]:
    """Retourne (bitrate_kbps, duration_s) depuis item.attributes."""
    bitrate = 0
    duration = 0.0
    for attr in getattr(item, "attributes", []):
        if attr.key == 0:
            bitrate = int(attr.value or 0)
        elif attr.key == 1:
            duration = float(attr.value or 0)
    return bitrate, duration


def _score(c: SearchCandidate, expected_dur: float, min_kbps: int) -> float:
    s = _FORMAT_SCORE.get(c.fmt, 0)
    # Qualité : FLAC (lossless) est toujours accepté ; MP3/WAV doivent atteindre min_kbps
    if c.fmt != "flac" and c.bitrate > 0 and c.bitrate < min_kbps:
        return -1.0
    s += min(c.bitrate, 1411) * 0.1
    # Durée
    if expected_dur > 0 and c.duration > 0:
        diff = abs(c.duration - expected_dur)
        if diff > 15:
            return -1.0
        s += (15 - diff) * 3
    # Slot libre
    if c.free_slot:
        s += 20
    return s


async def search(
    client,
    artist: str,
    title: str,
    expected_duration: Optional[float],
    accepted_formats: list[str],
    min_quality_kbps: int,
) -> list[SearchCandidate]:
    """
    Lance une recherche aioslsk et retourne les candidats scorés (meilleur en premier).
    `client` est un SoulSeekClient déjà démarré ET loggué (start() + login()).
    """
    query = textutil.clean_for_search(artist, title)
    accepted = {f.lower() for f in accepted_formats}

    await _throttle()   # espacement anti-flood (couvre aussi les renvois de la file)
    request = await client.searches.search(query)
    await asyncio.sleep(SEARCH_TIMEOUT_S)

    candidates: list[SearchCandidate] = []
    for result in getattr(request, "results", []):
        candidates.extend(
            candidates_from_result(result, expected_duration or 0, accepted, min_quality_kbps)
        )

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[:MAX_CANDIDATES]


def candidates_from_result(result, expected_dur: float, accepted: set, min_kbps: int) -> list[SearchCandidate]:
    """
    Transforme un SearchResult (résultat d'un peer) en candidats scorés et filtrés.
    Partagé par la recherche active et la wishlist (même logique de scoring).
    `accepted` = set d'extensions en minuscules.
    """
    out: list[SearchCandidate] = []
    for item in getattr(result, "shared_items", []):
        filename = os.path.basename((item.filename or "").replace("\\", "/"))
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in accepted:
            continue

        bitrate, duration = _extract_attributes(item)
        c = SearchCandidate(
            username    = result.username,
            remote_path = item.filename,
            filename    = filename,
            fmt         = ext,
            size        = int(getattr(item, "filesize", 0) or 0),
            bitrate     = bitrate,
            duration    = duration,
            free_slot   = bool(getattr(result, "has_free_slots", False)),
        )
        c.score = _score(c, expected_dur or 0, min_kbps)
        if c.score >= 0:
            out.append(c)
    return out


# ── SPEC-SLSK-008 : recherche manuelle (la fenêtre SLSK de DekkR) ───────────────
#
# Contrairement à `search()` (15 s d'attente puis 5 candidats), la recherche manuelle rend la main
# tout de suite et expose les résultats AU FIL DE L'EAU : `request.results` grossit pendant 15 s.
# Elle passe par le même espacement anti-flood (`_throttle`).

import time
import uuid

MANUAL_TTL_S     = 600    # résultats gardés 10 min (D10)
MANUAL_MAX_FILES = 200    # fichiers renvoyés au plus (D4)


@dataclass
class ManualSearch:
    search_id:  str
    artist:     str
    title:      str
    query:      str
    created_at: float                 # time.monotonic()
    started_at: Optional[float] = None  # départ effectif vers Soulseek (après l'espacement)
    request:    object = None
    error:      Optional[str] = None


_manual: dict[str, ManualSearch] = {}
_manual_tasks: set = set()


def _prune_manual() -> None:
    now = time.monotonic()
    for sid in [s for s, m in _manual.items() if now - m.created_at > MANUAL_TTL_S]:
        _manual.pop(sid, None)


async def _launch(client, ms: ManualSearch) -> None:
    try:
        await _throttle()
        ms.request = await client.searches.search(ms.query)
        ms.started_at = time.monotonic()
    except Exception as e:   # noqa: BLE001 — remonté tel quel à DekkR
        ms.error = str(e) or e.__class__.__name__
        ms.started_at = time.monotonic()


def start_manual(client, artist: str, title: str) -> ManualSearch:
    """Enregistre la recherche et la lance en tâche de fond. Rend la main immédiatement."""
    _prune_manual()
    ms = ManualSearch(
        search_id=str(uuid.uuid4()), artist=artist or "", title=title or "",
        query=textutil.clean_for_search(artist, title), created_at=time.monotonic(),
    )
    _manual[ms.search_id] = ms
    task = asyncio.create_task(_launch(client, ms))
    _manual_tasks.add(task)
    task.add_done_callback(_manual_tasks.discard)
    return ms


def get_manual(search_id: str) -> Optional[ManualSearch]:
    _prune_manual()
    return _manual.get(search_id)


def _folder_of(remote_path: str) -> str:
    p = (remote_path or "").replace("\\", "/")
    return p.rsplit("/", 1)[0] + "/" if "/" in p else ""


def manual_candidates(ms: ManualSearch, accepted: set) -> list[tuple[SearchCandidate, object]]:
    """(candidat, résultat du pair) pour chaque fichier au format accepté, dédoublonnés."""
    out: list[tuple[SearchCandidate, object]] = []
    seen: set = set()
    for result in list(getattr(ms.request, "results", None) or []):
        for item in getattr(result, "shared_items", []):
            key = (result.username, item.filename)
            if key in seen:
                continue
            filename = os.path.basename((item.filename or "").replace("\\", "/"))
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in accepted:
                continue
            seen.add(key)
            bitrate, duration = _extract_attributes(item)
            c = SearchCandidate(
                username=result.username, remote_path=item.filename, filename=filename, fmt=ext,
                size=int(getattr(item, "filesize", 0) or 0), bitrate=bitrate, duration=duration,
                free_slot=bool(getattr(result, "has_free_slots", False)),
            )
            # Sans durée attendue ni plancher de débit : le débit trop bas est SIGNALÉ, pas caché (D4).
            c.score = _score(c, 0, 0)
            out.append((c, result))
    return out


def manual_view(ms: ManualSearch, accepted: set, min_kbps: int) -> dict:
    """État d'une recherche manuelle pour GET /query/{id}."""
    cands = manual_candidates(ms, accepted)
    rows = []
    for c, result in cands:
        below = c.fmt != "flac" and 0 < c.bitrate < min_kbps
        rows.append({
            "username":          c.username,
            "remote_path":       c.remote_path,
            "filename":          c.filename,
            "folder":            _folder_of(c.remote_path),
            "format":            c.fmt,
            "size":              c.size,
            "bitrate":           c.bitrate or None,
            "duration":          c.duration or None,
            "free_slot":         c.free_slot,
            "queue_size":        int(getattr(result, "queue_size", 0) or 0),
            "avg_speed":         int(getattr(result, "avg_speed", 0) or 0),
            "below_min_quality": below,
            "score":             round(c.score, 1),
        })
    # Les débits trop bas après les autres, puis le meilleur score d'abord.
    rows.sort(key=lambda r: (r["below_min_quality"], -r["score"]))
    elapsed = (time.monotonic() - ms.started_at) if ms.started_at is not None else 0.0
    done = ms.error is not None or (ms.started_at is not None and elapsed >= SEARCH_TIMEOUT_S)
    return {
        "search_id":   ms.search_id,
        "status":      "done" if done else "running",
        "error":       ms.error,
        "elapsed_s":   round(elapsed, 1),
        "peers":       len({c.username for c, _ in cands}),
        "total_files": len(rows),
        "results":     rows[:MANUAL_MAX_FILES],
    }


def find_manual_candidate(search_id: str, username: str, remote_path: str, accepted: set) -> Optional[SearchCandidate]:
    """Le fichier choisi, s'il figure BIEN dans les résultats de cette recherche (D6) ; sinon None."""
    ms = get_manual(search_id)
    if ms is None:
        return None
    for c, _ in manual_candidates(ms, accepted):
        if c.username == username and c.remote_path == remote_path:
            return c
    return None
