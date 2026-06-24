"""
Journal CSV des jobs dekkr-slsk.

Écrit une ligne par évènement (téléchargement réussi, rejet, échec...) pour
permettre de comparer ce qui a été DEMANDÉ (artiste/titre/bpm/key envoyés par
DekkR) avec ce qui a été TÉLÉCHARGÉ (nom de fichier, format, bitrate, bpm/durée
lus dans les tags).

Le fichier `dekkr-slsk_journal.csv` est créé dans le dossier de téléchargement.
Encodage utf-8-sig pour un affichage correct des accents dans Excel.
"""

import csv
import os
from datetime import datetime

FILENAME = "dekkr-slsk_journal.csv"

HEADER = [
    "horodatage",
    "statut",
    "artiste_demande",
    "titre_demande",
    "bpm_demande",
    "key_demande",
    "fichier_telecharge",
    "format",
    "bitrate_kbps",
    "taille_octets",
    "bpm_obtenu",
    "duree_obtenue_s",
    "key_obtenue",
    "ecart_bpm",
    "verification",
]


def _path(cfg) -> str:
    folder = getattr(cfg, "download_folder", "") or "."
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    return os.path.join(folder, FILENAME)


def _fmt(val, spec=None) -> str:
    if val is None or val == "":
        return ""
    if spec:
        try:
            return format(float(val), spec)
        except (ValueError, TypeError):
            return str(val)
    return str(val)


def log(cfg, job, statut: str, candidate=None, meta=None, verification: str = "") -> None:
    """Ajoute une ligne au journal. Ne lève jamais (échec silencieux).

    `meta` est le dict renvoyé par verifier.read_metadata (duration/bpm/key
    lus dans le fichier téléchargé), ou None.
    """
    meta = meta or {}
    row = {
        "horodatage":         datetime.now().isoformat(timespec="seconds"),
        "statut":             statut,
        "artiste_demande":    getattr(job, "artist", "") or "",
        "titre_demande":      getattr(job, "title", "") or "",
        "bpm_demande":        _fmt(getattr(job, "bpm", None), ".1f"),
        "key_demande":        getattr(job, "key", "") or "",
        "fichier_telecharge": getattr(candidate, "filename", "") if candidate else "",
        "format":             getattr(candidate, "fmt", "") if candidate else "",
        "bitrate_kbps":       _fmt(getattr(candidate, "bitrate", None)) if candidate else "",
        "taille_octets":      _fmt(getattr(candidate, "size", None)) if candidate else "",
        "bpm_obtenu":         _fmt(meta.get("bpm"), ".2f"),
        "duree_obtenue_s":    _fmt(meta.get("duration"), ".0f"),
        "key_obtenue":        meta.get("key") or "",
        "ecart_bpm":          "",
        "verification":       verification,
    }

    job_bpm = getattr(job, "bpm", None)
    res_bpm = meta.get("bpm")
    if job_bpm and res_bpm:
        try:
            row["ecart_bpm"] = format(abs(float(res_bpm) - float(job_bpm)), ".2f")
        except (ValueError, TypeError):
            pass

    path = _path(cfg)
    is_new = not os.path.exists(path)
    try:
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=HEADER)
            if is_new:
                writer.writeheader()
            writer.writerow(row)
    except OSError:
        pass
