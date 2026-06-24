"""
Verification post-téléchargement via métadonnées du fichier (mutagen).

- Durée    : lue depuis les headers audio — fiable sur MP3/FLAC/WAV.
- BPM      : lu depuis le tag TBPM (MP3) ou BPM (FLAC/OGG) si présent ;
             si absent, la vérif BPM est sautée (DekkR/Meyda l'établira à l'import).
- Tonalité : non vérifiée ici.
"""

from dataclasses import dataclass
from typing import Optional

import mutagen


class AnalyzerUnavailable(Exception):
    """Gardé pour compatibilité — non levé dans cette implémentation."""


@dataclass
class VerifyResult:
    ok: bool
    reason: str
    bpm: Optional[float] = None
    duration: Optional[float] = None
    key: Optional[str] = None
    source: str = "tags"


def _read_bpm_tag(audio) -> Optional[float]:
    tags = audio.tags
    if tags is None:
        return None
    # MP3 ID3 : TBPM
    if "TBPM" in tags:
        try:
            return float(str(tags["TBPM"]))
        except (ValueError, TypeError):
            pass
    # FLAC / OGG Vorbis / générique
    for key in ("bpm", "BPM"):
        val = tags.get(key)
        if val:
            try:
                raw = val[0] if isinstance(val, list) else str(val)
                return float(raw)
            except (ValueError, TypeError):
                pass
    return None


async def analyze_and_verify(
    file_path: str,
    expected_bpm: Optional[float],
    expected_duration: Optional[float],
    bpm_threshold: float,
    cloud_url: str = "",
    cloud_key: str = "",
) -> VerifyResult:
    try:
        audio = mutagen.File(file_path)
    except Exception as e:
        return VerifyResult(ok=False, reason=f"Fichier illisible : {e}")

    if audio is None or audio.info is None:
        return VerifyResult(ok=False, reason="Format audio non reconnu")

    got_duration = audio.info.length
    got_bpm = _read_bpm_tag(audio)

    # Vérification durée (bloquante)
    if expected_duration and expected_duration > 0 and got_duration > 0:
        if abs(got_duration - expected_duration) > 15:
            return VerifyResult(
                ok=False,
                reason=f"Durée: attendu {expected_duration:.0f}s, obtenu {got_duration:.0f}s (seuil ±15s)",
                bpm=got_bpm,
                duration=got_duration,
            )

    # Vérification BPM — seulement si le tag est présent dans le fichier
    if expected_bpm and expected_bpm > 0 and got_bpm and got_bpm > 0:
        if abs(got_bpm - expected_bpm) > bpm_threshold:
            return VerifyResult(
                ok=False,
                reason=f"BPM: attendu {expected_bpm:.1f}, obtenu {got_bpm:.2f} (seuil ±{bpm_threshold})",
                bpm=got_bpm,
                duration=got_duration,
            )

    return VerifyResult(ok=True, reason="ok", bpm=got_bpm, duration=got_duration)
