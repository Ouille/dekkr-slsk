"""
Gestionnaire de jobs : search → download → verify → done/failed/queued.

Chaque job lance sa PROPRE tâche asyncio dès sa création (modèle spawn-par-job).
Le parallélisme est illimité si cfg.max_workers <= 0, sinon plafonné par un
sémaphore à max_workers jobs simultanés.

États d'un job :
  (fichier déjà sur le disque) → done, reason "deja_present"
  searching → downloading → done
                          → (aucun candidat / tous downloads échoués) → queued (wishlist, SPEC-SLSK-003)
                          → failed (erreur de recherche)
  queued → done   (la wishlist a trouvé, reason "wishlist_trouve")
         → failed (retiré de la wishlist, reason "wishlist_retire")

SPEC-SLSK-007 D4 : un job ne disparaît JAMAIS de la mémoire. Avant, il était retiré
au passage en wishlist ; DekkR recevait alors 404 sur /status et affichait
« Démon injoignable » alors que le démon tournait.
"""

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Callable

import history

MAX_ATTEMPTS = 5      # candidats max avant mise en liste d'attente
MAX_FINISHED = 200    # jobs terminés gardés en mémoire (les plus récents)


class JobStatus(str, Enum):
    SEARCHING   = "searching"
    DOWNLOADING = "downloading"
    VERIFYING   = "verifying"
    DONE        = "done"
    FAILED      = "failed"
    QUEUED      = "queued"   # en wishlist Soulseek (recherche différée)


ACTIVE   = (JobStatus.SEARCHING, JobStatus.DOWNLOADING, JobStatus.VERIFYING)
TERMINAL = (JobStatus.DONE, JobStatus.FAILED)

# Codes de raison (SPEC-SLSK-007 D6) — DekkR les traduit, le détail va dans `error`.
REASON_AUCUN_CANDIDAT   = "aucun_candidat"
REASON_ECHECS_DOWNLOAD  = "echecs_download"
REASON_ERREUR_RECHERCHE = "erreur_recherche"
REASON_DEJA_PRESENT     = "deja_present"
REASON_WISHLIST_TROUVE  = "wishlist_trouve"
REASON_WISHLIST_RETIRE  = "wishlist_retire"
REASON_ECHEC_CHOISI     = "echec_choisi"     # SPEC-SLSK-008 D7 — fichier choisi à la main


@dataclass
class Job:
    job_id:            str
    artist:            str
    title:             str
    bpm:               Optional[float]
    duration:          Optional[float]
    key:               Optional[str]
    status:            JobStatus = JobStatus.SEARCHING
    file_path:         Optional[str]  = None
    analysis:          Optional[dict] = None
    error:             Optional[str]  = None
    reason:            Optional[str]  = None
    wish_query:        Optional[str]  = None
    retry_at:          Optional[datetime] = None
    attempts:          int = 0
    created_at:        datetime = field(default_factory=datetime.now)
    updated_at:        datetime = field(default_factory=datetime.now)
    # Téléchargement en cours (SPEC-SLSK-007 D7) — non sérialisés tels quels
    transfer:          Any = None
    candidate:         Any = None

    def progress(self) -> Optional[dict]:
        if self.status != JobStatus.DOWNLOADING or self.candidate is None:
            return None
        t = self.transfer
        size = getattr(t, "filesize", None) or getattr(self.candidate, "size", None)
        return {
            "bytes":   int(getattr(t, "bytes_transfered", 0) or 0) if t is not None else 0,
            "size":    int(size) if size else None,
            "peer":    getattr(self.candidate, "username", None),
            "format":  getattr(self.candidate, "fmt", None),
            "bitrate": getattr(self.candidate, "bitrate", None),
        }

    def to_dict(self) -> dict:
        return {
            "job_id":     self.job_id,
            "artist":     self.artist,
            "title":      self.title,
            "status":     self.status.value,
            "reason":     self.reason,
            "file_path":  self.file_path,
            "analysis":   self.analysis,
            "error":      self.error,
            "attempts":   self.attempts,
            "progress":   self.progress(),
            "retry_at":   self.retry_at.isoformat() if self.retry_at else None,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "updated_at": self.updated_at.isoformat(timespec="seconds"),
            "source":     "memory",
        }


_jobs:      dict[str, Job] = {}
_state_cb:  list[Callable] = []
_tasks:     set = set()                       # réfs sur les tâches en cours (évite le GC)
_semaphore: Optional[asyncio.Semaphore] = None
_client = None
_cfg = None
STARTED_AT = datetime.now()                   # sépare le journal des sessions précédentes (D8)


def init(client, cfg) -> None:
    """Stocke le client Soulseek + la config. max_workers<=0 => parallélisme illimité."""
    global _client, _cfg, _semaphore
    _client = client
    _cfg = cfg
    n = getattr(cfg, "max_workers", 0) or 0
    _semaphore = asyncio.Semaphore(n) if n > 0 else None
    import searcher
    searcher.set_search_delay(getattr(cfg, "search_delay_seconds", 3.0))


def register_state_callback(cb: Callable) -> None:
    _state_cb.append(cb)


def _notify() -> None:
    active = sum(1 for j in _jobs.values() if j.status in ACTIVE)
    try:
        import wishlist
        waiting = wishlist.count()   # la wishlist est la nouvelle "liste d'attente"
    except Exception:
        waiting = 0
    for cb in _state_cb:
        try:
            cb(active, waiting)
        except Exception:
            pass


def notify_state() -> None:
    """Rafraîchit l'état (badge tray). Appelé aussi par le module wishlist."""
    _notify()


def _spawn(coro) -> None:
    """Lance une coroutine en tâche de fond avec réf conservée."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _prune() -> None:
    """Borne la mémoire : ne garde que les MAX_FINISHED jobs terminés les plus récents."""
    finished = [j for j in _jobs.values() if j.status in TERMINAL]
    if len(finished) <= MAX_FINISHED:
        return
    finished.sort(key=lambda j: j.updated_at)
    for j in finished[: len(finished) - MAX_FINISHED]:
        _jobs.pop(j.job_id, None)


def create_job(artist: str, title: str, bpm: Optional[float], duration: Optional[float], key: Optional[str]) -> Job:
    job = Job(
        job_id   = str(uuid.uuid4()),
        artist   = artist,
        title    = title,
        bpm      = bpm,
        duration = duration,
        key      = key,
    )
    _jobs[job.job_id] = job
    _prune()
    _spawn(_process_job(job))   # démarre immédiatement, en parallèle
    return job


def create_chosen_job(candidate, artist: str, title: str) -> Job:
    """
    SPEC-SLSK-008 D7 — fichier CHOISI dans une recherche manuelle : le job démarre à
    `downloading` (pas de recherche) et, s'il échoue, NE passe PAS en liste d'attente
    (elle chercherait « artiste titre » et prendrait un autre fichier).
    Titre vide ⇒ le fichier garde son nom d'origine ; le job prend ce nom pour titre.
    """
    shown_title = title or os.path.splitext(candidate.filename)[0]
    job = Job(job_id=str(uuid.uuid4()), artist=artist or "", title=shown_title,
              bpm=None, duration=None, key=None, status=JobStatus.DOWNLOADING)
    job.candidate = candidate
    _jobs[job.job_id] = job
    _prune()
    _notify()
    _spawn(_run_chosen(job, candidate, rename=bool(artist and title)))
    return job


async def _run_chosen(job: Job, candidate, rename: bool) -> None:
    import downloader as _dl
    from verifier import read_metadata
    cfg = _cfg
    job.attempts = 1

    def _on_transfer(t, _job=job):
        _job.transfer = t

    try:
        file_path = await _dl.download(_client, candidate, on_transfer=_on_transfer)
    except Exception as e:
        _update(job, JobStatus.FAILED, reason=REASON_ECHEC_CHOISI, error=str(e))
        history.log(cfg, job, "echec_download", candidate=candidate, verification=str(e))
        return
    if rename:
        file_path = _dl.rename_to(file_path, job.artist, job.title)
    meta = read_metadata(file_path)
    _update(job, JobStatus.DONE, file_path=file_path, reason=None,
            analysis={"bpm": meta.get("bpm"), "duration": meta.get("duration"),
                      "key": meta.get("key"), "engine": "tags"})
    history.log(cfg, job, "telecharge", candidate=candidate, meta=meta,
                local_path=file_path, verification="choisi")
    if getattr(cfg, "share_enabled", True):
        import slsk_session
        _spawn(slsk_session.rescan_shares())


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def get_active_jobs() -> list[Job]:
    return [j for j in _jobs.values() if j.status in ACTIVE]


def all_jobs() -> list[Job]:
    """Tous les jobs en mémoire, du plus récent au plus ancien."""
    return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def _update(job: Job, status: JobStatus, **kwargs) -> None:
    job.status     = status
    job.updated_at = datetime.now()
    for k, v in kwargs.items():
        setattr(job, k, v)
    if status != JobStatus.DOWNLOADING:
        job.transfer = None
        job.candidate = None
    _notify()


def resolve_wish(query: str, ok: bool, file_path: Optional[str] = None) -> None:
    """
    La wishlist a résolu (ok=True, fichier trouvé ou déjà présent) ou retiré (ok=False)
    l'item `query` : les jobs en attente sur cette requête deviennent terminés.
    """
    for job in _jobs.values():
        if job.status != JobStatus.QUEUED or job.wish_query != query:
            continue
        if ok:
            _update(job, JobStatus.DONE, file_path=file_path, reason=REASON_WISHLIST_TROUVE)
        else:
            _update(job, JobStatus.FAILED, reason=REASON_WISHLIST_RETIRE,
                    error="Retiré de la liste d'attente")


async def _process_job(job: Job) -> None:
    """Traite un job de bout en bout. Respecte le sémaphore si défini."""
    if _semaphore is not None:
        async with _semaphore:
            await _run(job)
    else:
        await _run(job)


def _to_wishlist(job: Job, reason: str, error: Optional[str] = None) -> None:
    """Passe le job en liste d'attente — il RESTE en mémoire, statut queued (D4)."""
    import wishlist
    import textutil
    import downloader as _dl
    query = textutil.clean_for_search(job.artist, job.title)
    if not wishlist.add(job, reason) and not wishlist.has(query):
        # Refusé par la wishlist : le fichier est apparu entre-temps (autre job), ou la
        # requête est vide. Sans ce cas, le job resterait « en attente » pour toujours.
        existing = _dl.already_present(_cfg.download_folder, job.artist, job.title, _cfg.accepted_formats)
        if existing:
            _update(job, JobStatus.DONE, file_path=existing, reason=REASON_DEJA_PRESENT)
        else:
            _update(job, JobStatus.FAILED, reason=reason, error=error or "Mise en liste d'attente impossible")
        return
    _update(job, JobStatus.QUEUED, reason=reason, error=error, wish_query=query)


async def _run(job: Job) -> None:
    """
    Télécharge le meilleur candidat texte et journalise les faits.
    AUCUNE vérification BPM/durée : dekkr-slsk est un téléchargeur honnête,
    le jugement "bon track ?" se fait via le journal + l'écoute (ou DekkR/Meyda).
    """
    import searcher as _searcher
    import downloader as _dl
    from verifier import read_metadata
    cfg = _cfg

    # Déjà sur le disque : inutile d'interroger Soulseek, DekkR l'ingère tel quel (D4).
    existing = _dl.already_present(cfg.download_folder, job.artist, job.title, cfg.accepted_formats)
    if existing:
        meta = read_metadata(existing)
        _update(job, JobStatus.DONE, file_path=existing, reason=REASON_DEJA_PRESENT,
                analysis={"bpm": meta.get("bpm"), "duration": meta.get("duration"),
                          "key": meta.get("key"), "engine": "tags"})
        history.log(cfg, job, "deja_present", meta=meta, local_path=existing,
                    verification="fichier deja present")
        return

    _update(job, JobStatus.SEARCHING)

    try:
        candidates = await _searcher.search(
            client            = _client,
            artist            = job.artist,
            title             = job.title,
            expected_duration = job.duration,
            accepted_formats  = cfg.accepted_formats,
            min_quality_kbps  = cfg.min_quality_kbps,
        )
    except Exception as e:
        _update(job, JobStatus.FAILED, reason=REASON_ERREUR_RECHERCHE, error=f"Erreur recherche : {e}")
        history.log(cfg, job, "erreur_recherche", verification=str(e))
        return

    if not candidates:
        # Recherche différée via wishlist (remplace le retry actif — ADR-020)
        _to_wishlist(job, REASON_AUCUN_CANDIDAT)
        return

    last_error: Optional[str] = None
    for candidate in candidates:
        job.attempts += 1
        _update(job, JobStatus.DOWNLOADING)
        job.candidate = candidate

        def _on_transfer(t, _job=job):
            _job.transfer = t

        try:
            file_path = await _dl.download(_client, candidate, on_transfer=_on_transfer)
        except Exception as e:
            last_error = str(e)
            history.log(cfg, job, "echec_download", candidate=candidate, verification=str(e))
            continue  # essayer le candidat suivant

        # Renommage « Artiste - Titre.ext » (nom = clé dans DekkR)
        file_path = _dl.rename_to(file_path, job.artist, job.title)

        # Lecture des métadonnées réelles (durée fiable) — sans jugement
        meta = read_metadata(file_path)
        _update(job, JobStatus.DONE,
                file_path=file_path,
                reason=None,
                analysis={
                    "bpm":      meta.get("bpm"),
                    "duration": meta.get("duration"),
                    "key":      meta.get("key"),
                    "engine":   "tags",
                })
        history.log(cfg, job, "telecharge", candidate=candidate, meta=meta,
                    local_path=file_path, verification="non verifie")
        # SPEC-SLSK-005 (D5) : re-scanner les partages pour exposer le nouveau fichier (si partage actif).
        if getattr(cfg, "share_enabled", True):
            import slsk_session
            _spawn(slsk_session.rescan_shares())
        return

    # Tous les candidats ont échoué au téléchargement → recherche différée (wishlist)
    _to_wishlist(job, REASON_ECHECS_DOWNLOAD,
                 error=f"{job.attempts} essai(s) échoué(s) — dernier : {last_error}" if last_error else None)
