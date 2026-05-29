"""
Recherche et scoring de résultats sur le réseau Soulseek via aioslsk.

Le client SoulSeekClient est partagé — créé une fois dans slsk_session.py
et passé en paramètre pour éviter de se connecter/déconnecter à chaque recherche.
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional

SEARCH_TIMEOUT_S = 15    # secondes d'attente pour les résultats
MAX_CANDIDATES   = 5

# Priorité de format : plus la valeur est haute, mieux c'est
_FORMAT_SCORE = {"flac": 300, "mp3": 200, "wav": 100}


@dataclass
class SearchCandidate:
    username: str
    remote_path: str
    filename: str
    fmt: str          # extension normalisée ('mp3', 'flac', ...)
    size: int
    bitrate: int
    duration: float
    free_slot: bool
    score: float = 0.0


def _score(c: SearchCandidate, expected_dur: float, min_kbps: int) -> float:
    # Format
    s = _FORMAT_SCORE.get(c.fmt, 0)
    # Qualité minimale
    if c.bitrate > 0 and c.bitrate < min_kbps:
        return -1.0
    s += min(c.bitrate, 1411) * 0.1   # 1411 = FLAC ~1411 kbps max
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

    `client` est un SoulSeekClient déjà démarré (voir slsk_session.py).

    API aioslsk 1.6.x :
      request = await client.searches.search(query)
      await asyncio.sleep(SEARCH_TIMEOUT_S)
      # request.results : list[SearchResult]
      # result.username : str
      # result.shared_items : list[SharedItem]
      # item.filename : str (chemin distant Windows, séparateur backslash)
      # item.size : int (octets)
      # item.bit_rate : int (kbps, peut être 0 si inconnu)
      # item.length : int (secondes, peut être 0)
      # result.free_upload_slots : int
    """
    query = f"{artist} {title}".strip()
    accepted = {f.lower() for f in accepted_formats}

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

            bitrate  = int(getattr(item, "bit_rate", 0) or 0)
            duration = float(getattr(item, "length",   0) or 0)
            c = SearchCandidate(
                username    = result.username,
                remote_path = item.filename,
                filename    = filename,
                fmt         = ext,
                size        = int(item.size or 0),
                bitrate     = bitrate,
                duration    = duration,
                free_slot   = int(getattr(result, "free_upload_slots", 0) or 0) > 0,
            )
            c.score = _score(c, expected_duration or 0, min_quality_kbps)
            if c.score >= 0:
                candidates.append(c)

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[:MAX_CANDIDATES]
