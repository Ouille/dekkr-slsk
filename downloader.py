"""
Téléchargement d'un fichier depuis un peer Soulseek via aioslsk.

Le client est partagé (voir slsk_session.py).

API aioslsk 1.6.x :
  transfer = await client.transfers.download(username, remote_path, local_path)
  # transfer.is_complete() -> bool
  # transfer.is_failed()   -> bool
  # transfer.state         -> état textuel
"""

import asyncio
import os

from searcher import SearchCandidate

POLL_INTERVAL    = 0.5   # secondes entre vérifications de progression
DOWNLOAD_TIMEOUT = 300   # 5 min max par téléchargement


async def download(client, candidate: SearchCandidate, download_folder: str) -> str:
    """
    Télécharge `candidate` vers `download_folder`.
    Retourne le chemin local du fichier téléchargé.
    Lève RuntimeError si le téléchargement échoue ou dépasse le timeout.
    """
    os.makedirs(download_folder, exist_ok=True)
    local_path = os.path.join(download_folder, candidate.filename)

    transfer = await client.transfers.download(
        candidate.username,
        candidate.remote_path,
        local_path,
    )

    elapsed = 0.0
    while not transfer.is_complete():
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        if transfer.is_failed():
            raise RuntimeError(f"Téléchargement échoué : {transfer.state}")
        if elapsed > DOWNLOAD_TIMEOUT:
            raise RuntimeError("Timeout téléchargement (5 min)")

    if not os.path.exists(local_path):
        raise RuntimeError("Fichier introuvable après téléchargement")

    return local_path


def delete_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
