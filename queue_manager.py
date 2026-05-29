"""
Gestionnaire de jobs : search → download → verify → done/failed/queued.

Chaque job passe par les états :
  searching → downloading → verifying → done
                                      → failed (retry si < MAX_ATTEMPTS)
                                      → queued (tous candidats épuisés, retry planifié)
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Callable

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


_jobs:    dict[str, Job] = {}
_queue:   asyncio.Queue  = asyncio.Queue()
_semaphore: Optional[asyncio.Semaphore] = None
_state_cb: list[Callable] = []


def init(max_workers: int) -> None:
    global _semaphore
    _semaphore = asyncio.Semaphore(max_workers)


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
    _queue.put_nowait(job)
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


async def run_worker(client, cfg) -> None:
    """Boucle worker — traite les jobs en continu."""
    import searcher as _searcher
    import downloader as _dl
    from verifier import analyze_and_verify, AnalyzerUnavailable

    while True:
        job = await _queue.get()

        # Retry différé : si retry_at dans le futur, remettre en queue
        if job.retry_at and datetime.now() < job.retry_at:
            delay = (job.retry_at - datetime.now()).total_seconds()
            await asyncio.sleep(max(0, delay))

        await _semaphore.acquire()
        try:
            await _process_job(job, client, cfg, _searcher, _dl, analyze_and_verify, AnalyzerUnavailable)
        finally:
            _semaphore.release()
            _queue.task_done()


async def _process_job(job, client, cfg, _searcher, _dl, analyze_and_verify, AnalyzerUnavailable) -> None:
    from verifier import AnalyzerUnavailable as _AU

    _update(job, JobStatus.SEARCHING)

    try:
        candidates = await _searcher.search(
            client       = client,
            artist       = job.artist,
            title        = job.title,
            expected_duration = job.duration,
            accepted_formats  = cfg.accepted_formats,
            min_quality_kbps  = cfg.min_quality_kbps,
        )
    except Exception as e:
        _update(job, JobStatus.FAILED, error=f"Erreur recherche : {e}")
        return

    if not candidates:
        _update(job, JobStatus.QUEUED,
                error="no_candidate_found",
                retry_at=datetime.now() + timedelta(minutes=cfg.retry_delay_minutes))
        _queue.put_nowait(job)
        return

    for candidate in candidates:
        job.attempts += 1
        _update(job, JobStatus.DOWNLOADING)

        try:
            file_path = await _dl.download(client, candidate)
        except Exception as e:
            continue  # essayer le candidat suivant

        _update(job, JobStatus.VERIFYING)

        try:
            result = await analyze_and_verify(
                file_path        = file_path,
                expected_bpm     = job.bpm,
                expected_duration = job.duration,
                bpm_threshold    = cfg.bpm_threshold,
                cloud_url        = cfg.analyzer_cloud_url,
                cloud_key        = cfg.analyzer_cloud_key,
            )
        except _AU:
            # Analyzer indisponible — mettre en pause sans supprimer le fichier
            _update(job, JobStatus.QUEUED,
                    file_path=file_path,
                    error="analyzer_unavailable",
                    retry_at=datetime.now() + timedelta(minutes=5))
            _queue.put_nowait(job)
            return

        if result.ok:
            _update(job, JobStatus.DONE,
                    file_path=file_path,
                    analysis={
                        "bpm":      result.bpm,
                        "duration": result.duration,
                        "key":      result.key,
                        "engine":   result.source.value if result.source else None,
                    })
            return
        else:
            _dl.delete_file(file_path)
            # Essayer le candidat suivant

    # Tous les candidats épuisés
    _update(job, JobStatus.QUEUED,
            error="all_candidates_failed",
            retry_at=datetime.now() + timedelta(minutes=cfg.retry_delay_minutes))
    _queue.put_nowait(job)
