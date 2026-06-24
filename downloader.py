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
import textutil

POLL_INTERVAL    = 1.0    # secondes entre vérifications
DOWNLOAD_TIMEOUT = 300    # 5 min max


async def _cleanup_failed(client, transfer) -> None:
    """Annule le transfert et supprime le fichier partiel laissé sur le disque."""
    local_path = getattr(transfer, "local_path", None)
    try:
        # abort() annule le transfert ET supprime le fichier (cas download)
        await client.transfers.abort(transfer)
    except Exception:
        pass
    # Filet de sécurité si abort n'a pas supprimé (état déjà finalisé, etc.)
    if local_path and os.path.exists(local_path):
        try:
            os.remove(local_path)
        except OSError:
            pass


async def download(client, candidate: SearchCandidate) -> str:
    """
    Lance le téléchargement de `candidate` via le client aioslsk.
    Le dossier de destination est celui configuré dans settings.shares.download.
    Retourne le chemin local absolu du fichier téléchargé.
    Lève RuntimeError si le téléchargement échoue ou dépasse le timeout.
    En cas d'échec/timeout, le fichier partiel est supprimé.
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
            await _cleanup_failed(client, transfer)
            raise RuntimeError("Timeout téléchargement (5 min)")

    if transfer.state.VALUE != TransferState.COMPLETE:
        await _cleanup_failed(client, transfer)
        raise RuntimeError(f"Téléchargement échoué : {transfer.state.VALUE}")

    local_path = transfer.local_path
    if not local_path or not os.path.exists(local_path):
        raise RuntimeError("Fichier introuvable après téléchargement")

    return local_path


def _unique_path(path: str) -> str:
    """Si `path` existe déjà, ajoute un suffixe ' (2)', ' (3)'... pour ne pas écraser."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def rename_to(path: str, artist: str, title: str) -> str:
    """
    Renomme le fichier téléchargé en « Artiste - Titre.ext ».
    Le nom sert de clé dans DekkR — on veut un format explicite et stable.
    Retourne le nouveau chemin (ou l'ancien en cas d'échec).
    """
    if not path or not os.path.exists(path):
        return path
    folder = os.path.dirname(path)
    ext = os.path.splitext(path)[1].lower()
    new_name = textutil.safe_filename(artist, title, ext)
    new_path = os.path.join(folder, new_name)

    if os.path.abspath(new_path) == os.path.abspath(path):
        return path  # déjà au bon nom

    new_path = _unique_path(new_path)
    try:
        os.replace(path, new_path)
        return new_path
    except OSError:
        return path  # on garde le fichier d'origine si le renommage échoue


def delete_file(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
