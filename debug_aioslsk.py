"""
Script de debug aioslsk — dump la structure réelle des résultats de recherche.
Lance ce script directement (pas besoin que dekkr-slsk tourne).

Usage :
  pip install aioslsk
  python debug_aioslsk.py
"""

import asyncio
import json
import os
import sys

try:
    from aioslsk.client import SoulSeekClient
    from aioslsk.settings import Settings, CredentialsSettings, NetworkSettings, ListeningSettings
except ImportError:
    print("❌ aioslsk non installé — pip install aioslsk")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────
# Charge depuis config.json de dekkr-slsk si présent, sinon demande

def load_credentials():
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    cfg_path = os.path.join(appdata, "dekkr-slsk", "config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        username = d.get("soulseek_username", "")
        # Le mot de passe est chiffré — on demande en clair pour le debug
        print(f"Username trouvé dans config : {username}")
        password = input("Mot de passe Soulseek (en clair pour ce test) : ").strip()
        return username, password
    except FileNotFoundError:
        username = input("Username Soulseek : ").strip()
        password = input("Mot de passe Soulseek : ").strip()
        return username, password


QUERY        = "Silver Panda We call this acid"
WAIT_SECONDS = 15


async def main():
    username, password = load_credentials()

    # Ports différents de dekkr-slsk.exe (60000/60001) pour coexister
    settings = Settings(
        credentials=CredentialsSettings(username=username, password=password),
        network=NetworkSettings(
            listening=ListeningSettings(port=61000, obfuscated_port=61001)
        ),
    )

    print(f"\n🔌 Connexion à Soulseek…")
    client = SoulSeekClient(settings)
    await client.start()
    print("✅ Connecté")

    print(f"\n🔍 Recherche : '{QUERY}' ({WAIT_SECONDS}s d'attente)…")
    request = await client.searches.search(QUERY)
    await asyncio.sleep(WAIT_SECONDS)

    results = getattr(request, "results", [])
    print(f"\n📦 Résultats bruts : {len(results)} peers")

    if not results:
        print("⚠️  Aucun résultat — vérifier connexion ou query")
        # Dump l'objet request pour voir ses attributs
        print(f"\nattributs de request : {[a for a in dir(request) if not a.startswith('_')]}")
        await client.stop()
        return

    # Dump le premier résultat en détail
    first = results[0]
    print(f"\n--- Premier résultat ---")
    print(f"type : {type(first)}")
    print(f"attributs : {[a for a in dir(first) if not a.startswith('_')]}")
    for attr in dir(first):
        if attr.startswith("_"):
            continue
        try:
            val = getattr(first, attr)
            if not callable(val):
                print(f"  {attr} = {repr(val)[:120]}")
        except Exception as e:
            print(f"  {attr} → erreur : {e}")

    # Dump les fichiers partagés
    items = getattr(first, "shared_items", None) \
         or getattr(first, "files", None) \
         or getattr(first, "results", None) \
         or []
    print(f"\n--- Fichiers (via shared_items/files/results) : {len(items)} ---")

    if items:
        item = items[0]
        print(f"type item : {type(item)}")
        for attr in dir(item):
            if attr.startswith("_"):
                continue
            try:
                val = getattr(item, attr)
                if not callable(val):
                    print(f"  {attr} = {repr(val)[:120]}")
            except Exception as e:
                print(f"  {attr} → erreur : {e}")

    print(f"\n✅ Debug terminé — {len(results)} peers, {len(items)} fichiers dans le 1er")
    await client.stop()


asyncio.run(main())
