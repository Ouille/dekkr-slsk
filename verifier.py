"""
Lecture des métadonnées d'un fichier téléchargé (mutagen).

dekkr-slsk ne juge PAS si le fichier est "le bon" — il télécharge et
journalise les faits. La vérification BPM/key réelle se fait ailleurs
(DekkR/Meyda à l'import, ou à l'oreille).

read_metadata() ne lève jamais : retourne {} si le fichier est illisible.
  - duration : durée réelle lue dans les headers (fiable MP3/FLAC/WAV)
  - bpm      : tag TBPM/BPM si présent (souvent absent, surtout en FLAC)
  - key      : tag TKEY/INITIALKEY si présent
"""

import mutagen


def _read_bpm_tag(audio):
    tags = audio.tags
    if tags is None:
        return None
    if "TBPM" in tags:
        try:
            return float(str(tags["TBPM"]))
        except (ValueError, TypeError):
            pass
    for key in ("bpm", "BPM"):
        val = tags.get(key)
        if val:
            try:
                raw = val[0] if isinstance(val, list) else str(val)
                return float(raw)
            except (ValueError, TypeError):
                pass
    return None


def _read_key_tag(audio):
    tags = audio.tags
    if tags is None:
        return None
    if "TKEY" in tags:
        try:
            return str(tags["TKEY"]) or None
        except (ValueError, TypeError):
            pass
    for key in ("initialkey", "INITIALKEY", "key", "KEY"):
        val = tags.get(key)
        if val:
            raw = val[0] if isinstance(val, list) else str(val)
            return str(raw) or None
    return None


def read_metadata(file_path: str) -> dict:
    """Retourne {duration, bpm, key} lus dans le fichier. {} si illisible."""
    try:
        audio = mutagen.File(file_path)
    except Exception:
        return {}
    if audio is None or audio.info is None:
        return {}
    return {
        "duration": getattr(audio.info, "length", None),
        "bpm":      _read_bpm_tag(audio),
        "key":      _read_key_tag(audio),
    }
