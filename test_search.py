"""
Script de test dekkr-slsk — usage développeur uniquement.

Lance une recherche Soulseek et affiche le résultat + cohérence avec les valeurs attendues.

Usage :
  python test_search.py                        # lit test_input.json
  python test_search.py mon_test.json          # fichier custom
  python test_search.py --artist "Daft Punk" --title "Get Lucky" --bpm 116 --duration 248

Format test_input.json :
  {
    "artist":   "Daft Punk",
    "title":    "Get Lucky",
    "bpm":      116.0,
    "duration": 248.0,
    "key":      "11A"
  }
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌ requests non installé — pip install requests")
    sys.exit(1)

BASE_URL    = "http://localhost:7431"
POLL_DELAY  = 3   # secondes entre chaque poll
POLL_TIMEOUT = 600  # 10 min max


def check_server() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=2)
        data = r.json()
        print(f"✅ dekkr-slsk actif — connecté Soulseek : {data.get('connected', '?')}")
        return True
    except Exception:
        print("❌ dekkr-slsk ne répond pas sur localhost:7431 — lancez l'app d'abord")
        return False


def submit_search(artist: str, title: str, bpm: float, duration: float, key: str) -> str:
    payload = {"artist": artist, "title": title}
    if bpm:      payload["bpm"]      = bpm
    if duration: payload["duration"] = duration
    if key:      payload["key"]      = key

    r = requests.post(f"{BASE_URL}/search", json=payload, timeout=5)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    print(f"\n🔍 Job lancé : {job_id}")
    print(f"   Recherche : {artist} — {title}")
    if bpm:      print(f"   BPM attendu  : {bpm}")
    if duration: print(f"   Durée attendue: {duration}s")
    if key:      print(f"   Tonalité attendue: {key}")
    return job_id


def poll_status(job_id: str, expected_bpm: float, expected_duration: float, expected_key: str) -> None:
    print("\n⏳ En attente du résultat…")
    elapsed = 0
    last_status = ""

    while elapsed < POLL_TIMEOUT:
        try:
            r = requests.get(f"{BASE_URL}/status/{job_id}", timeout=5)
            data = r.json()
        except Exception as e:
            print(f"   Erreur polling : {e}")
            time.sleep(POLL_DELAY)
            elapsed += POLL_DELAY
            continue

        status = data.get("status", "?")
        if status != last_status:
            icons = {
                "searching":   "🔍 Recherche en cours…",
                "downloading": "⬇️  Téléchargement…",
                "verifying":   "🔬 Vérification…",
                "done":        "✅ Terminé",
                "failed":      "❌ Échoué",
                "queued":      "⏳ Mis en liste d'attente",
            }
            print(f"   [{elapsed:3d}s] {icons.get(status, status)}")
            last_status = status

        if status == "done":
            print_result(data, expected_bpm, expected_duration, expected_key)
            return

        if status == "failed":
            print(f"\n❌ Échec : {data.get('error', 'raison inconnue')}")
            return

        if status == "queued":
            retry_at = data.get("retry_at", "inconnu")
            print(f"\n⏳ Mis en liste d'attente — prochain retry : {retry_at}")
            print(f"   Raison : {data.get('error', '?')}")
            return

        time.sleep(POLL_DELAY)
        elapsed += POLL_DELAY

    print("\n⏰ Timeout — le job n'a pas abouti en 10 minutes")


def print_result(data: dict, expected_bpm: float, expected_duration: float, expected_key: str) -> None:
    analysis = data.get("analysis") or {}
    got_bpm      = analysis.get("bpm")
    got_duration = analysis.get("duration")
    got_key      = analysis.get("key")
    engine       = analysis.get("engine", "?")
    file_path    = data.get("file_path", "—")

    print(f"\n{'='*55}")
    print(f"  RÉSULTAT")
    print(f"{'='*55}")
    print(f"  Fichier    : {file_path}")
    print(f"  Engine     : {engine}")
    print()

    # BPM
    if expected_bpm and got_bpm:
        diff = abs(got_bpm - expected_bpm)
        ok = "✅" if diff <= 2.0 else "⚠️ "
        print(f"  BPM        : {got_bpm:.2f}  (attendu {expected_bpm:.1f}, écart {diff:.2f}) {ok}")
    elif got_bpm:
        print(f"  BPM        : {got_bpm:.2f}")

    # Durée
    if expected_duration and got_duration:
        diff = abs(got_duration - expected_duration)
        ok = "✅" if diff <= 15 else "⚠️ "
        print(f"  Durée      : {got_duration:.1f}s  (attendu {expected_duration:.0f}s, écart {diff:.1f}s) {ok}")
    elif got_duration:
        print(f"  Durée      : {got_duration:.1f}s")

    # Tonalité
    if expected_key and got_key:
        ok = "✅" if got_key == expected_key else "ℹ️ "
        print(f"  Tonalité   : {got_key}  (attendu {expected_key}) {ok}")
    elif got_key:
        print(f"  Tonalité   : {got_key}")

    print(f"{'='*55}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test dekkr-slsk")
    parser.add_argument("input_file", nargs="?", default="test_input.json")
    parser.add_argument("--artist",   default="")
    parser.add_argument("--title",    default="")
    parser.add_argument("--bpm",      type=float, default=0)
    parser.add_argument("--duration", type=float, default=0)
    parser.add_argument("--key",      default="")
    args = parser.parse_args()

    # CLI args prioritaires sur le fichier
    if args.artist and args.title:
        artist, title = args.artist, args.title
        bpm, duration, key = args.bpm, args.duration, args.key
    else:
        p = Path(args.input_file)
        if not p.exists():
            print(f"❌ Fichier introuvable : {p}")
            print("   Créez test_input.json ou passez --artist / --title")
            sys.exit(1)
        d = json.loads(p.read_text(encoding="utf-8"))
        artist   = d.get("artist", "")
        title    = d.get("title", "")
        bpm      = float(d.get("bpm", 0) or 0)
        duration = float(d.get("duration", 0) or 0)
        key      = d.get("key", "")

    if not artist or not title:
        print("❌ artist et title sont requis")
        sys.exit(1)

    if not check_server():
        sys.exit(1)

    job_id = submit_search(artist, title, bpm, duration, key)
    poll_status(job_id, bpm, duration, key)


if __name__ == "__main__":
    main()
