from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import queue_manager
import history

VERSION = "1.1.0"

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

# SPEC-SLSK-007 D12 : avant, `allow_origins=["*"]` — n'importe quel site ouvert dans le
# navigateur pouvait lancer des téléchargements, et lirait l'historique via /jobs.
DEFAULT_ALLOWED_ORIGINS = [
    "https://dekk-r.com",
    "https://www.dekk-r.com",
    "http://localhost:5173",
]
_cors_installed = False
_cfg = None


def install_cors(origins: Optional[list] = None) -> None:
    """À appeler AVANT le démarrage d'uvicorn (Starlette fige les middlewares au 1er appel)."""
    global _cors_installed
    if _cors_installed:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins or DEFAULT_ALLOWED_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    _cors_installed = True


def set_config(cfg) -> None:
    """Config lue par /jobs (dossier du journal)."""
    global _cfg
    _cfg = cfg


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


# ── SPEC-SLSK-007 : suivi des téléchargements ──────────────────────────────────

JOURNAL_LIMIT = 20


@app.get("/jobs")
async def jobs():
    """Jobs de la session (mémoire) + les 20 dernières demandes terminées des sessions précédentes."""
    out = [j.to_dict() for j in queue_manager.all_jobs()]
    if _cfg is not None:
        out += history.read_recent(_cfg, before=queue_manager.STARTED_AT, limit=JOURNAL_LIMIT)
    return {"jobs": out}


@app.get("/wishlist")
async def get_wishlist():
    import wishlist
    return {"items": wishlist.to_dicts()}


class WishRemoveRequest(BaseModel):
    query: str


@app.post("/wishlist/remove", status_code=204)
async def remove_wish(req: WishRemoveRequest):
    import wishlist
    if not wishlist.remove_now(req.query):
        raise HTTPException(404, detail="Absent de la liste d'attente")
    return Response(status_code=204)
