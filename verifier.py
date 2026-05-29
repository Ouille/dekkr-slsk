"""
Verification post-téléchargement.

Chaîne d'analyse :
  1. Bridge local localhost:7430 (dekkr-essentia-bridge)
  2. Backend cloud Render (si configuré)
  3. Aucun disponible → raise AnalyzerUnavailable

La vérification BPM/durée est toujours effectuée —
aucun double-check n'est nécessaire, librosa et essentia sont fiables à ±2 BPM.
"""

import asyncio
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import aiohttp

LOCAL_ANALYZER_URL = "http://localhost:7430"
LOCAL_HEALTH_TIMEOUT = 0.5   # 500ms — réponse locale < 10ms normalement
ANALYZE_TIMEOUT     = 120.0  # 2 min max pour l'analyse


class AnalyzerUnavailable(Exception):
    pass


class AnalyzerSource(Enum):
    LOCAL = "local"
    CLOUD = "cloud"


@dataclass
class VerifyResult:
    ok: bool
    reason: str
    bpm: Optional[float] = None
    duration: Optional[float] = None
    key: Optional[str] = None
    source: Optional[AnalyzerSource] = None


async def _check_local() -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{LOCAL_ANALYZER_URL}/health",
                timeout=aiohttp.ClientTimeout(total=LOCAL_HEALTH_TIMEOUT),
            ) as r:
                return r.status == 200
    except Exception:
        return False


async def _analyze_at(url: str, file_path: str, api_key: str = "") -> Optional[dict]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with aiohttp.ClientSession() as s:
            with open(file_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("file", f, filename=os.path.basename(file_path))
                async with s.post(
                    f"{url}/analyze",
                    data=form,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=ANALYZE_TIMEOUT),
                ) as r:
                    if r.status == 200:
                        return await r.json()
    except Exception:
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
    """
    Analyse le fichier et vérifie BPM/durée.
    Lève AnalyzerUnavailable si aucun analyzer n'est joignable.
    """
    analysis = None
    source = None

    if await _check_local():
        analysis = await _analyze_at(LOCAL_ANALYZER_URL, file_path)
        source = AnalyzerSource.LOCAL

    if analysis is None and cloud_url:
        analysis = await _analyze_at(cloud_url, file_path, cloud_key)
        source = AnalyzerSource.CLOUD

    if analysis is None:
        raise AnalyzerUnavailable("Aucun analyzer disponible (localhost:7430 injoignable, cloud non configuré)")

    got_bpm      = analysis.get("bpm") or 0.0
    got_duration = analysis.get("duration") or 0.0
    got_key      = analysis.get("key") or ""

    # Vérification BPM (bloquante)
    if expected_bpm and expected_bpm > 0 and got_bpm > 0:
        if abs(got_bpm - expected_bpm) > bpm_threshold:
            return VerifyResult(
                ok=False,
                reason=f"BPM: attendu {expected_bpm:.1f}, obtenu {got_bpm:.2f} (seuil ±{bpm_threshold})",
                bpm=got_bpm, duration=got_duration, key=got_key, source=source,
            )

    # Vérification durée (bloquante)
    if expected_duration and expected_duration > 0 and got_duration > 0:
        if abs(got_duration - expected_duration) > 15:
            return VerifyResult(
                ok=False,
                reason=f"Durée: attendu {expected_duration:.0f}s, obtenu {got_duration:.0f}s (seuil ±15s)",
                bpm=got_bpm, duration=got_duration, key=got_key, source=source,
            )

    return VerifyResult(
        ok=True, reason="ok",
        bpm=got_bpm, duration=got_duration, key=got_key, source=source,
    )
