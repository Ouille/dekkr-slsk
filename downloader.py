"""
Téléchargement d'un fichier depuis un peer Soulseek via aioslsk.

API aioslsk 1.6.x (vérifiée) :
  client.transfers.download(username, remote_path) -> Transfer
  transfer.is_finalized()    -> True quand COMPLETE / ABORTED / FAILED
  transfer.state.VALUE       -> TransferState enum
  transfer.local_path        -> chemin local (défini après démarrage)

Le dossier de destination est configuré via settings.shares.download
dans slsk_session.connect().
"""

import asyncio
import os

from aioslsk.transfer.model import TransferState

from searcher import SearchCandidate

POLL_INTERVAL    = 1.0    # secondes entre vérifications
DOWNLOAD_TIMEOUT = 300    # 5 min max


async def download(client, candidate: SearchCandidate) -> str:
    """
    Lance le téléchargement de `candidate` via le client aioslsk.
    Le dossier de destination est celui configuré dans settings.shares.download.
    Retourne le chemin local absolu du fichier téléchargé.
    Lève RuntimeError si le téléchargement échoue ou dépasse le timeout.
    """
    transfer = await client.transfers.download(
        candidate.username,
        candidate.remote_path,
    )

    elapsed = 0.0
    while not transfer.is_finalized():
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        if elapsed > DOWNLOAD_TIMEOUT:
            raise RuntimeError("Timeout téléchargement (5 min)")

    if transfer.state.VALUE != TransferState.COMPLETE:
        raise RuntimeError(f"Téléchargement échoué : {transfer.state.VALUE}")

    local_path = transfer.local_path
    if not local_path or not os.path.exists(local_path):
        raise RuntimeError("Fichier introuvable après téléchargement")

    return local_path


def delete_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
