from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import queue_manager

VERSION = "1.0.3"

_slsk_connected = False
_download_folder: Optional[str] = None
_on_job_created: list = []


def set_connected(val: bool) -> None:
    global _slsk_connected
    _slsk_connected = val


def set_download_folder(path: Optional[str]) -> None:
    """Dossier de téléchargement exposé via /health (consommé par DekkR — SPEC-SLSK-004)."""
    global _download_folder
    _download_folder = path


def register_job_callback(cb) -> None:
    _on_job_created.append(cb)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="dekkr-slsk", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status":          "ok",
        "version":         VERSION,
        "connected":       _slsk_connected,
        "download_folder": _download_folder,
    }


class SearchRequest(BaseModel):
    artist:   str
    title:    str
    bpm:      Optional[float] = None
    duration: Optional[float] = None
    key:      Optional[str]   = None


@app.post("/search", status_code=202)
async def search(req: SearchRequest):
    if not _slsk_connected:
        raise HTTPException(503, detail="Non connecté au réseau Soulseek")
    job = queue_manager.create_job(
        artist   = req.artist,
        title    = req.title,
        bpm      = req.bpm,
        duration = req.duration,
        key      = req.key,
    )
    for cb in _on_job_created:
        try:
            cb(job)
        except Exception:
            pass
    return {"job_id": job.job_id}


@app.get("/status/{job_id}")
async def status(job_id: str):
    job = queue_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job introuvable")
    return job.to_dict()
