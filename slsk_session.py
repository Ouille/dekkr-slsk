"""
Gestion du client SoulSeekClient (connexion persistante).

Le client est créé une fois, partagé par searcher.py et downloader.py.
La connexion est maintenue en arrière-plan via asyncio.
"""

import asyncio
import logging
from typing import Optional

from aioslsk.client import SoulSeekClient
from aioslsk.events import SearchResultEvent
from aioslsk.settings import (
    Settings, CredentialsSettings, SharesSettings, SharedDirectorySettingEntry,
    NetworkSettings, ListeningSettings, PeerSettings,
)

logger = logging.getLogger(__name__)

_client: Optional[SoulSeekClient] = None
_connected = False
_on_connect_change: list = []
_scanning = False   # garde anti-empilement des re-scans de partage (SPEC-SLSK-005)


def register_connect_callback(cb) -> None:
    _on_connect_change.append(cb)


def _notify(connected: bool) -> None:
    global _connected
    _connected = connected
    for cb in _on_connect_change:
        try:
            cb(connected)
        except Exception:
            pass


def get_client() -> Optional[SoulSeekClient]:
    return _client


async def rescan_shares() -> None:
    """Ré-indexe les partages (SPEC-SLSK-005, D5) après un téléchargement.

    Gardé par `_scanning` pour éviter d'empiler plusieurs scans en téléchargement parallèle.
    """
    global _scanning
    if _client is None or _scanning:
        return
    _scanning = True
    try:
        await _client.shares.scan()
    except Exception as e:
        logger.warning("Re-scan des partages échoué : %s", e)
    finally:
        _scanning = False


def register_result_handler(handler) -> None:
    """Abonne un handler (async) aux résultats de recherche (dont wishlist)."""
    if _client is not None:
        _client.events.register(SearchResultEvent, handler)


def is_connected() -> bool:
    return _connected


async def connect(username: str, password: str, download_folder: str,
                  share_enabled: bool = True) -> SoulSeekClient:
    """Crée et connecte le client Soulseek. Retourne le client.

    SPEC-SLSK-005 : si share_enabled, on partage le download_folder (récursif) sur Soulseek.
    scan_on_start=True (défaut aioslsk) indexe les fichiers au démarrage.
    """
    global _client
    directories = [SharedDirectorySettingEntry(path=download_folder)] if share_enabled else []
    settings = Settings(
        credentials=CredentialsSettings(username=username, password=password),
        shares=SharesSettings(download=download_folder, directories=directories),
        network=NetworkSettings(
            listening=ListeningSettings(port=60000, obfuscated_port=60001),
            peer=PeerSettings(obfuscate=True),
        ),
    )
    _client = SoulSeekClient(settings)
    await asyncio.wait_for(_client.start(), timeout=15)
    await asyncio.wait_for(_client.login(), timeout=30)
    _notify(True)
    return _client


async def disconnect() -> None:
    global _client
    if _client:
        try:
            await _client.stop()
        except Exception:
            pass
        _client = None
    _notify(False)


async def test_connection(username: str, password: str) -> tuple[bool, str]:
    """Test de connexion pour la fenêtre de setup."""
    try:
        settings = Settings(
            credentials=CredentialsSettings(username=username, password=password),
            network=NetworkSettings(
                listening=ListeningSettings(port=60000, obfuscated_port=60001),
                peer=PeerSettings(obfuscate=True),
            ),
        )
        client = SoulSeekClient(settings)
        await asyncio.wait_for(client.start(), timeout=15)
        await asyncio.wait_for(client.login(), timeout=30)
        await client.stop()
        return True, "Connexion réussie"
    except asyncio.TimeoutError:
        return False, "Timeout — réseau Soulseek injoignable"
    except Exception as e:
        return False, str(e)
