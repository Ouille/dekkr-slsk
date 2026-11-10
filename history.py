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
    "fichier_renomme",
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


def log(cfg, job, statut: str, candidate=None, meta=None, local_path: str = "",
        verification: str = "") -> None:
    """Ajoute une ligne au journal. Ne lève jamais (échec silencieux).

    `meta`       : dict de verifier.read_metadata (duration/bpm/key du fichier), ou None.
    `local_path` : chemin du fichier APRÈS renommage (colonne fichier_renomme).
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
        "fichier_renomme":    os.path.basename(local_path) if local_path else "",
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
    write_header = _needs_header(path)
    try:
        # utf-8-sig (avec BOM) seulement à la création/réinit ; sinon utf-8 pur
        # pour ne pas insérer de BOM au milieu du fichier en mode append.
        encoding = "utf-8-sig" if write_header else "utf-8"
        with open(path, "a", newline="", encoding=encoding) as f:
            writer = csv.DictWriter(f, fieldnames=HEADER)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
    except OSError:
        pass


def _needs_header(path: str) -> bool:
    """True si le fichier est neuf ou si son en-tête est obsolète.

    Si l'en-tête ne correspond plus (ajout de colonnes), l'ancien journal est
    archivé en `.old` et un fichier neuf est démarré — évite des colonnes
    décalées.
    """
    if not os.path.exists(path):
        return True
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            first = f.readline().rstrip("\r\n")
    except OSError:
        return False
    if first == ",".join(HEADER):
        return False
    # En-tête obsolète : archiver l'ancien journal
    try:
        bak = path + ".old"
        n = 1
        while os.path.exists(bak):
            bak = f"{path}.old{n}"
            n += 1
        os.replace(path, bak)
    except OSError:
        pass
    return True


# ── Lecture : historique des sessions précédentes (SPEC-SLSK-007 D8) ───────────

# Statut du journal → (statut de job, code de raison). Les autres lignes (échec d'un
# candidat, ajout en wishlist) ne terminent pas une demande : elles sont ignorées.
_TERMINAL_ROWS = {
    "telecharge":       ("done",   None),
    "wishlist_trouve":  ("done",   "wishlist_trouve"),
    "deja_present":     ("done",   "deja_present"),
    "erreur_recherche": ("failed", "erreur_recherche"),
}


def read_recent(cfg, before: datetime, limit: int = 20) -> list[dict]:
    """
    Les `limit` dernières demandes TERMINÉES du journal, horodatées avant `before`
    (le démarrage du démon : les jobs de la session courante sont déjà en mémoire).
    Format identique à Job.to_dict, `source: "journal"`. Ne lève jamais.
    """
    path = _path(cfg)
    rows: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for i, r in enumerate(csv.DictReader(f)):
                mapped = _TERMINAL_ROWS.get(r.get("statut") or "")
                if not mapped:
                    continue
                try:
                    at = datetime.fromisoformat(r.get("horodatage") or "")
                except ValueError:
                    continue
                if at >= before:
                    continue
                status, reason = mapped
                ts = at.isoformat(timespec="seconds")
                rows.append({
                    "job_id":     f"journal:{i}",
                    "artist":     r.get("artiste_demande") or "",
                    "title":      r.get("titre_demande") or "",
                    "status":     status,
                    "reason":     reason,
                    "file_path":  r.get("fichier_renomme") or None,
                    "analysis":   None,
                    "error":      (r.get("verification") or None) if status == "failed" else None,
                    "attempts":   0,
                    "progress":   None,
                    "retry_at":   None,
                    "created_at": ts,
                    "updated_at": ts,
                    "source":     "journal",
                })
    except (OSError, csv.Error):
        return []
    return rows[-limit:][::-1]
