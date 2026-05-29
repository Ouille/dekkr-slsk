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
    from aioslsk.settings import Settings, CredentialsSettings, NetworkSettings, ListeningSettings, PeerSettings
    from aioslsk.events import SearchResultEvent
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


QUERY        = "Silver Panda acid"   # query courte = plus de résultats
WAIT_AFTER_CONNECT = 5    # laisser la connexion P2P s'établir
WAIT_SECONDS = 40         # Soulseek est lent sur une connexion fraîche


async def main():
    username, password = load_credentials()

    # Port 60000 = port par défaut Soulseek, déjà autorisé par Windows Firewall
    # si dekkr-slsk.exe a tourné une fois (popup "Autoriser cette app")
    # IMPORTANT : fermer dekkr-slsk.exe avant de lancer ce script
    settings = Settings(
        credentials=CredentialsSettings(username=username, password=password),
        network=NetworkSettings(
            listening=ListeningSettings(port=60000, obfuscated_port=60001),
            peer=PeerSettings(obfuscate=True),
        ),
    )

    print(f"\n🔌 Connexion à Soulseek…")
    client = SoulSeekClient(settings)

    # Compteur de résultats via événements (indépendant du polling)
    event_results = []
    async def on_search_result(event: SearchResultEvent):
        event_results.append(event.result)

    client.events.register(SearchResultEvent, on_search_result)

    await client.start()
    print(f"✅ Connecté — attente {WAIT_AFTER_CONNECT}s pour stabiliser la connexion P2P…")
    await asyncio.sleep(WAIT_AFTER_CONNECT)

    print(f"\n🔍 Recherche : '{QUERY}' (attente {WAIT_SECONDS}s)…")
    request = await client.searches.search(QUERY)

    # Afficher la progression toutes les 5s (via events ET via request.results)
    for i in range(0, WAIT_SECONDS, 5):
        await asyncio.sleep(5)
        print(f"   [{i+5}s] résultats polling={len(getattr(request, 'results', []))}  events={len(event_results)}")

    # Fusionner résultats polling + events
    results = getattr(request, "results", [])
    all_results = results if results else event_results
    print(f"\n📦 Résultats : {len(results)} via polling / {len(event_results)} via events")

    if not all_results:
        print("⚠️  Aucun résultat reçu")
        print(f"   attributs de request : {[a for a in dir(request) if not a.startswith('_')]}")
        print("   → Vérifier : firewall Windows autorise les connexions entrantes sur port 61000 ?")
        print("   → Ou relancer avec dekkr-slsk.exe fermé sur port 60000")
        await client.stop()
        return

    results = all_results

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
