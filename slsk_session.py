"""
Gestion du client SoulSeekClient (connexion persistante).

Le client est créé une fois, partagé par searcher.py et downloader.py.
La connexion est maintenue en arrière-plan via asyncio.
"""

import asyncio
import logging
from typing import Optional

from aioslsk.client import SoulSeekClient
from aioslsk.settings import Settings, CredentialsSettings, SharesSettings, NetworkSettings, ListeningSettings, PeerSettings

logger = logging.getLogger(__name__)

_client: Optional[SoulSeekClient] = None
_connected = False
_on_connect_change: list = []


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


def is_connected() -> bool:
    return _connected


async def connect(username: str, password: str, download_folder: str) -> SoulSeekClient:
    """Crée et connecte le client Soulseek. Retourne le client."""
    global _client
    settings = Settings(
        credentials=CredentialsSettings(username=username, password=password),
        shares=SharesSettings(download=download_folder),
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
