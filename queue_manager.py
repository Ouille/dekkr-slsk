"""
Gestionnaire de jobs : search → download → verify → done/failed/queued.

Chaque job lance sa PROPRE tâche asyncio dès sa création (modèle spawn-par-job).
Le parallélisme est illimité si cfg.max_workers <= 0, sinon plafonné par un
sémaphore à max_workers jobs simultanés.

États d'un job :
  searching → downloading → verifying → done
                                      → queued (aucun/tous candidats échoués, retry planifié)
                                      → failed (erreur de recherche)
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Callable

import history

MAX_ATTEMPTS = 5   # candidats max avant mise en liste d'attente


class JobStatus(str, Enum):
    SEARCHING   = "searching"
    DOWNLOADING = "downloading"
    VERIFYING   = "verifying"
    DONE        = "done"
    FAILED      = "failed"
    QUEUED      = "queued"   # en attente de retry automatique


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
    retry_at:          Optional[datetime] = None
    attempts:          int = 0
    created_at:        datetime = field(default_factory=datetime.now)
    updated_at:        datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "job_id":    self.job_id,
            "status":    self.status.value,
            "file_path": self.file_path,
            "analysis":  self.analysis,
            "error":     self.error,
            "retry_at":  self.retry_at.isoformat() if self.retry_at else None,
        }


_jobs:      dict[str, Job] = {}
_state_cb:  list[Callable] = []
_tasks:     set = set()                       # réfs sur les tâches en cours (évite le GC)
_semaphore: Optional[asyncio.Semaphore] = None
_client = None
_cfg = None


def init(client, cfg) -> None:
    """Stocke le client Soulseek + la config. max_workers<=0 => parallélisme illimité."""
    global _client, _cfg, _semaphore
    _client = client
    _cfg = cfg
    n = getattr(cfg, "max_workers", 0) or 0
    _semaphore = asyncio.Semaphore(n) if n > 0 else None


def register_state_callback(cb: Callable) -> None:
    _state_cb.append(cb)


def _notify() -> None:
    active  = sum(1 for j in _jobs.values() if j.status in (JobStatus.SEARCHING, JobStatus.DOWNLOADING, JobStatus.VERIFYING))
    waiting = sum(1 for j in _jobs.values() if j.status == JobStatus.QUEUED)
    for cb in _state_cb:
        try:
            cb(active, waiting)
        except Exception:
            pass


def _spawn(coro) -> None:
    """Lance une coroutine en tâche de fond avec réf conservée."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _schedule_retry(job: Job, delay_seconds: float) -> None:
    """Re-traite un job après `delay_seconds`, sans bloquer aucune tâche active."""
    async def _later():
        try:
            await asyncio.sleep(max(0, delay_seconds))
        except asyncio.CancelledError:
            return
        job.retry_at = None
        _spawn(_process_job(job))
    _spawn(_later())


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
    _spawn(_process_job(job))   # démarre immédiatement, en parallèle
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def get_active_jobs() -> list[Job]:
    return [j for j in _jobs.values() if j.status in (JobStatus.SEARCHING, JobStatus.DOWNLOADING, JobStatus.VERIFYING)]


def get_queued_jobs() -> list[Job]:
    return [j for j in _jobs.values() if j.status == JobStatus.QUEUED]


def _update(job: Job, status: JobStatus, **kwargs) -> None:
    job.status     = status
    job.updated_at = datetime.now()
    for k, v in kwargs.items():
        setattr(job, k, v)
    _notify()


async def _process_job(job: Job) -> None:
    """Traite un job de bout en bout. Respecte le sémaphore si défini."""
    if _semaphore is not None:
        async with _semaphore:
            await _run(job)
    else:
        await _run(job)


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
        _update(job, JobStatus.FAILED, error=f"Erreur recherche : {e}")
        history.log(cfg, job, "erreur_recherche", verification=str(e))
        return

    if not candidates:
        _update(job, JobStatus.QUEUED,
                error="no_candidate_found",
                retry_at=datetime.now() + timedelta(minutes=cfg.retry_delay_minutes))
        history.log(cfg, job, "aucun_candidat", verification="aucun resultat correspondant")
        _schedule_retry(job, cfg.retry_delay_minutes * 60)
        return

    for candidate in candidates:
        job.attempts += 1
        _update(job, JobStatus.DOWNLOADING)

        try:
            file_path = await _dl.download(_client, candidate)
        except Exception as e:
            history.log(cfg, job, "echec_download", candidate=candidate, verification=str(e))
            continue  # essayer le candidat suivant

        # Lecture des métadonnées réelles (durée fiable) — sans jugement
        meta = read_metadata(file_path)
        _update(job, JobStatus.DONE,
                file_path=file_path,
                analysis={
                    "bpm":      meta.get("bpm"),
                    "duration": meta.get("duration"),
                    "key":      meta.get("key"),
                    "engine":   "tags",
                })
        history.log(cfg, job, "telecharge", candidate=candidate, meta=meta,
                    verification="non verifie")
        return

    # Tous les candidats ont échoué au téléchargement
    _update(job, JobStatus.QUEUED,
            error="all_downloads_failed",
            retry_at=datetime.now() + timedelta(minutes=cfg.retry_delay_minutes))
    history.log(cfg, job, "echec_tous_telechargements",
                verification=f"{job.attempts} tentative(s) echouee(s)")
    _schedule_retry(job, cfg.retry_delay_minutes * 60)
