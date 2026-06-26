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
        for item in getattr(result, "shared_items", []):
            filename = os.path.basename(
                (item.filename or "").replace("\\", "/")
            )
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
            c.score = _score(c, expected_duration or 0, min_quality_kbps)
            if c.score >= 0:
                candidates.append(c)

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[:MAX_CANDIDATES]
