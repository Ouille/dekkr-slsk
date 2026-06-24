"""
Nettoyage des noms artiste/titre venus d'Octav.

Deux usages :
  - clean_for_search : requête Soulseek (retire les (2) Discogs + ponctuation)
  - safe_filename    : nom de fichier "Artiste - Titre.ext" (sans (2), sans
                       caractères interdits par le système de fichiers)

Le suffixe de désambiguïsation Discogs « (2) », « (15) »... en fin de nom
d'artiste fausse la recherche et n'a rien à faire dans le nom de fichier.
"""

import re

# « Aurora (2) », « Mike (15) » -> suffixe en toute fin de chaîne uniquement
_DISAMBIG_RE = re.compile(r"\s*\(\d+\)\s*$")
# apostrophes droites/typographiques/backtick : supprimées (Don't -> Dont)
_APOS_RE = re.compile(r"[’'`]")
# toute autre ponctuation (hors lettres/chiffres/_/espaces unicode)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
# caractères interdits dans un nom de fichier Windows
_FS_INVALID_RE = re.compile(r'[\\/:*?"<>|]')
_WS_RE = re.compile(r"\s+")


def strip_disambiguation(artist: str) -> str:
    """Retire le suffixe Discogs « (2) » en fin de nom d'artiste."""
    if not artist:
        return artist or ""
    return _DISAMBIG_RE.sub("", artist).strip()


def clean_for_search(artist: str, title: str) -> str:
    """Requête de recherche nettoyée : sans (2), sans ponctuation parasite."""
    a = strip_disambiguation(artist or "")
    raw = f"{a} {title or ''}"
    raw = _APOS_RE.sub("", raw)        # Don't -> Dont
    raw = _PUNCT_RE.sub(" ", raw)      # -, &, /, !, ... -> espace
    return _WS_RE.sub(" ", raw).strip()


def safe_filename(artist: str, title: str, ext: str) -> str:
    """Nom de fichier « Artiste - Titre.ext » sûr pour le système de fichiers."""
    a = strip_disambiguation(artist or "").strip()
    t = (title or "").strip()
    if a and t:
        base = f"{a} - {t}"
    else:
        base = a or t or "track"
    base = _FS_INVALID_RE.sub("", base)   # retire \ / : * ? " < > |
    base = _WS_RE.sub(" ", base).strip(" .")  # pas d'espace/point en fin (Windows)
    if not base:
        base = "track"
    ext = ext if ext.startswith(".") else f".{ext}"
    return f"{base}{ext}"
