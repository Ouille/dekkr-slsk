"""
Tests SPEC-SLSK-007 — suivi des téléchargements (jobs persistants, /jobs, /wishlist, CORS).

Usage : python -m unittest test_slsk007 -v
Aucun accès réseau : Soulseek, le téléchargement et les métadonnées sont remplacés par des faux.
"""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

_TMP = tempfile.mkdtemp(prefix="slsk007-")
os.environ["APPDATA"] = _TMP          # wishlist.json ira ici, pas dans le vrai %APPDATA%

import downloader       # noqa: E402
import history          # noqa: E402
import queue_manager as qm  # noqa: E402
import searcher         # noqa: E402
import server           # noqa: E402
import wishlist         # noqa: E402
from queue_manager import JobStatus  # noqa: E402


def _cand(name="a.mp3", user="peer1", size=1000):
    return SimpleNamespace(username=user, filename=name, remote_path=f"@@x\\{name}",
                           fmt="mp3", bitrate=320, size=size, score=1.0)


class _FakeClient:
    def __init__(self):
        self.settings = SimpleNamespace(searches=SimpleNamespace(wishlist=[]))


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir=_TMP)
        self.cfg = SimpleNamespace(
            download_folder=self.folder, accepted_formats=["mp3", "flac"],
            min_quality_kbps=0, max_workers=0, search_delay_seconds=0,
            share_enabled=False, wishlist_collect_seconds=0,
        )
        qm._jobs.clear()
        wishlist._items.clear()
        client = _FakeClient()
        qm._client, qm._cfg, qm._semaphore = client, self.cfg, None
        wishlist._client, wishlist._cfg, wishlist._loop = client, self.cfg, None
        self._orig = (searcher.search, downloader.download, history.log)
        history.log = lambda *a, **k: None
        self.search_calls = 0

    def tearDown(self):
        searcher.search, downloader.download, history.log = self._orig
        shutil.rmtree(self.folder, ignore_errors=True)

    def run_job(self, artist="Artiste", title="Titre"):
        job = qm.Job(job_id="j1", artist=artist, title=title, bpm=None, duration=None, key=None)
        qm._jobs[job.job_id] = job
        asyncio.run(qm._run(job))
        return job

    def stub_search(self, candidates):
        async def fake(**kw):
            self.search_calls += 1
            return list(candidates)
        searcher.search = fake


class JobLifecycle(Base):
    def test_aucun_candidat_reste_en_memoire_en_attente(self):
        self.stub_search([])
        job = self.run_job()
        self.assertIs(qm.get_job("j1"), job, "le job ne doit plus disparaître (D4)")
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(job.reason, "aucun_candidat")
        self.assertEqual(job.to_dict()["status"], "queued")
        self.assertTrue(wishlist.has(job.wish_query))

    def test_echecs_download_en_attente_avec_le_dernier_motif(self):
        self.stub_search([_cand("a.mp3"), _cand("b.mp3")])

        async def boom(client, c, on_transfer=None):
            raise RuntimeError("Timeout téléchargement (5 min)")
        downloader.download = boom
        job = self.run_job()
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(job.reason, "echecs_download")
        self.assertIn("2 essai", job.error)
        self.assertIn("Timeout", job.error)

    def test_deja_present_termine_sans_chercher(self):
        path = os.path.join(self.folder, "Artiste - Titre.mp3")
        open(path, "wb").close()
        self.stub_search([_cand()])
        job = self.run_job()
        self.assertEqual(job.status, JobStatus.DONE)
        self.assertEqual(job.reason, "deja_present")
        self.assertEqual(job.file_path, path)
        self.assertEqual(self.search_calls, 0)

    def test_erreur_recherche(self):
        async def fail(**kw):
            raise RuntimeError("réseau")
        searcher.search = fail
        job = self.run_job()
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.reason, "erreur_recherche")

    def test_progression_pendant_le_telechargement(self):
        self.stub_search([_cand(size=5000)])
        seen = {}

        async def slow(client, c, on_transfer=None):
            t = SimpleNamespace(bytes_transfered=1234, filesize=5000)
            on_transfer(t)
            seen["progress"] = qm.get_job("j1").to_dict()["progress"]
            p = os.path.join(self.folder, "tmp.mp3")
            open(p, "wb").close()
            return p
        downloader.download = slow
        job = self.run_job()
        self.assertEqual(seen["progress"], {"bytes": 1234, "size": 5000, "peer": "peer1",
                                            "format": "mp3", "bitrate": 320})
        self.assertEqual(job.status, JobStatus.DONE)
        self.assertIsNone(job.to_dict()["progress"])

    def test_wishlist_trouvee_termine_le_job(self):
        self.stub_search([])
        job = self.run_job()
        wishlist._finish(wishlist._items[job.wish_query], "D:/x.mp3")
        self.assertEqual(job.status, JobStatus.DONE)
        self.assertEqual(job.reason, "wishlist_trouve")
        self.assertEqual(job.file_path, "D:/x.mp3")

    def test_retrait_de_la_wishlist_termine_le_job_en_echec(self):
        self.stub_search([])
        job = self.run_job()
        self.assertTrue(wishlist.remove_now(job.wish_query))
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.reason, "wishlist_retire")
        self.assertFalse(wishlist.remove_now(job.wish_query))


class Journal(Base):
    def test_read_recent_ne_garde_que_les_sessions_precedentes_et_les_fins(self):
        start = datetime(2026, 11, 10, 12, 0, 0)
        path = os.path.join(self.folder, history.FILENAME)
        rows = [
            ("2026-11-10T10:00:00", "telecharge", "A", "Un"),
            ("2026-11-10T10:01:00", "echec_download", "B", "Deux"),     # ignorée
            ("2026-11-10T10:02:00", "erreur_recherche", "C", "Trois"),
            ("2026-11-10T13:00:00", "telecharge", "D", "Quatre"),         # session courante
        ]
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=history.HEADER)
            w.writeheader()
            for ts, st, a, t in rows:
                w.writerow({"horodatage": ts, "statut": st, "artiste_demande": a, "titre_demande": t,
                            "verification": "boom" if st == "erreur_recherche" else ""})
        out = history.read_recent(self.cfg, before=start, limit=20)
        self.assertEqual([(r["artist"], r["status"]) for r in out], [("C", "failed"), ("A", "done")])
        self.assertTrue(all(r["source"] == "journal" for r in out))
        self.assertEqual(out[0]["error"], "boom")
        self.assertEqual(len(history.read_recent(self.cfg, before=start, limit=1)), 1)


async def _asgi(app, method, path, headers=(), body=b""):
    """Appel ASGI minimal (httpx/TestClient absents de l'environnement du démon)."""
    sent = []
    msgs = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        sent.append(m)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "server": ("127.0.0.1", 7431), "client": ("127.0.0.1", 5000),
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, data


class Http(Base):
    @classmethod
    def setUpClass(cls):
        server.install_cors(None)

    def test_jobs_et_wishlist(self):
        self.stub_search([])
        self.run_job()
        server.set_config(self.cfg)
        status, _, body = asyncio.run(_asgi(server.app, "GET", "/jobs"))
        self.assertEqual(status, 200)
        jobs = json.loads(body)["jobs"]
        self.assertEqual([(j["job_id"], j["status"], j["reason"]) for j in jobs],
                         [("j1", "queued", "aucun_candidat")])
        status, _, body = asyncio.run(_asgi(server.app, "GET", "/wishlist"))
        items = json.loads(body)["items"]
        self.assertEqual([(i["artist"], i["reason"]) for i in items], [("Artiste", "aucun_candidat")])
        q = json.dumps({"query": items[0]["query"]}).encode()
        hdr = [("content-type", "application/json")]
        self.assertEqual(asyncio.run(_asgi(server.app, "POST", "/wishlist/remove", hdr, q))[0], 204)
        self.assertEqual(asyncio.run(_asgi(server.app, "POST", "/wishlist/remove", hdr, q))[0], 404)
        self.assertEqual(qm.get_job("j1").status, JobStatus.FAILED)

    def test_status_d_un_job_en_attente_ne_rend_plus_404(self):
        self.stub_search([])
        self.run_job()
        status, _, body = asyncio.run(_asgi(server.app, "GET", "/status/j1"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "queued")

    def test_cors_dekkr_autorise_autre_site_refuse(self):
        def origin_of(o):
            _, h, _ = asyncio.run(_asgi(server.app, "GET", "/health", [("origin", o)]))
            return h.get("access-control-allow-origin")
        self.assertEqual(origin_of("https://dekk-r.com"), "https://dekk-r.com")
        self.assertEqual(origin_of("http://localhost:5173"), "http://localhost:5173")
        self.assertIsNone(origin_of("https://evil.example"))


if __name__ == "__main__":
    unittest.main()
