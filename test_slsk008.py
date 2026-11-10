"""
Tests SPEC-SLSK-008 — recherche manuelle et téléchargement d'un fichier choisi.

Usage : python -m unittest test_slsk008 -v
Aucun accès réseau : la recherche Soulseek et le téléchargement sont remplacés par des faux.
"""

import asyncio
import json
import os
import unittest
from types import SimpleNamespace

from test_slsk007 import Base, _asgi   # même décor (APPDATA temporaire, faux client), même appel ASGI

import downloader
import queue_manager as qm
import searcher
import server
import slsk_session
import wishlist
from queue_manager import JobStatus


def _item(path, size=1000, bitrate=None, duration=300):
    attrs = []
    if bitrate is not None:
        attrs.append(SimpleNamespace(key=0, value=bitrate))
    attrs.append(SimpleNamespace(key=1, value=duration))
    return SimpleNamespace(filename=path, filesize=size, attributes=attrs)


def _result(user, items, free=True, queue=0, speed=0):
    return SimpleNamespace(username=user, shared_items=items, has_free_slots=free,
                           queue_size=queue, avg_speed=speed)


class _Searches:
    def __init__(self, results):
        self.results = results
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        return SimpleNamespace(results=self.results)


class Manual(Base):
    def setUp(self):
        super().setUp()
        self._orig8 = (searcher.SEARCH_TIMEOUT_S, slsk_session._client)
        searcher.set_search_delay(0)
        searcher._manual.clear()
        self.cfg.min_quality_kbps = 192
        self.results = [
            _result("lent", [_item("@@a\\Musique\\Artiste\\Titre.mp3", bitrate=128)]),
            _result("bon", [_item("@@b\\Rips\\Artiste - Titre.flac", size=40_000_000),
                            _item("@@b\\Rips\\Artiste - Titre.flac", size=40_000_000),   # doublon
                            _item("@@b\\Rips\\cover.jpg")], queue=3, speed=900),
            _result("mp3", [_item("@@c\\x\\Titre (Edit).mp3", bitrate=320)], free=False),
        ]
        self.searches = _Searches(self.results)
        qm._client.searches = self.searches
        slsk_session._client = qm._client
        server.set_config(self.cfg)
        server.set_connected(True)

    def tearDown(self):
        searcher.SEARCH_TIMEOUT_S, slsk_session._client = self._orig8
        super().tearDown()

    async def _started(self, artist="Artiste", title="Titre"):
        ms = searcher.start_manual(qm._client, artist, title)
        for _ in range(5):
            await asyncio.sleep(0)
        return ms

    def test_resultats_filtres_dedoublonnes_tries_debit_bas_signale(self):
        async def go():
            ms = await self._started()
            return searcher.manual_view(ms, {"mp3", "flac", "wav"}, 192)
        v = asyncio.run(go())
        self.assertEqual(self.searches.queries, ["Artiste Titre"])
        self.assertEqual(v["status"], "running")
        self.assertEqual(v["total_files"], 3, "jpg exclu, doublon fusionné")
        self.assertEqual(v["peers"], 3)
        self.assertEqual([r["username"] for r in v["results"]], ["bon", "mp3", "lent"])
        self.assertEqual([r["below_min_quality"] for r in v["results"]], [False, False, True])
        flac = v["results"][0]
        self.assertEqual((flac["folder"], flac["queue_size"], flac["avg_speed"], flac["bitrate"]),
                         ("@@b/Rips/", 3, 900, None))

    def test_termine_apres_le_delai(self):
        searcher.SEARCH_TIMEOUT_S = 0

        async def go():
            return searcher.manual_view(await self._started(), {"mp3"}, 0)
        self.assertEqual(asyncio.run(go())["status"], "done")

    def test_plafond_des_resultats(self):
        self.searches.results[:] = [_result(f"u{i}", [_item(f"@@u\\{i}.mp3", bitrate=320)]) for i in range(250)]

        async def go():
            return searcher.manual_view(await self._started(), {"mp3"}, 0)
        v = asyncio.run(go())
        self.assertEqual((v["total_files"], len(v["results"])), (250, searcher.MANUAL_MAX_FILES))

    def _http(self, coro_factory):
        return asyncio.run(coro_factory())

    def test_http_query_puis_download_d_un_resultat(self):
        downloaded = {}

        async def fake_dl(client, c, on_transfer=None):
            downloaded["path"] = c.remote_path
            p = os.path.join(self.folder, c.filename)
            open(p, "wb").close()
            return p
        downloader.download = fake_dl
        hdr = [("content-type", "application/json")]

        async def go():
            st, _, body = await _asgi(server.app, "POST", "/query", hdr, json.dumps({"artist": "Artiste", "title": "Titre"}).encode())
            self.assertEqual(st, 202)
            sid = json.loads(body)["search_id"]
            for _ in range(5):
                await asyncio.sleep(0)
            st, _, body = await _asgi(server.app, "GET", f"/query/{sid}")
            first = json.loads(body)["results"][0]
            st, _, body = await _asgi(server.app, "POST", "/download", hdr, json.dumps({
                "search_id": sid, "username": first["username"], "remote_path": first["remote_path"],
                "artist": "Artiste", "title": "Titre"}).encode())
            self.assertEqual(st, 202)
            job_id = json.loads(body)["job_id"]
            for _ in range(10):
                await asyncio.sleep(0)
            return job_id
        job = qm.get_job(asyncio.run(go()))
        self.assertEqual(downloaded["path"], "@@b\\Rips\\Artiste - Titre.flac")
        self.assertEqual(job.status, JobStatus.DONE)
        self.assertEqual(os.path.basename(job.file_path), "Artiste - Titre.flac")

    def test_download_refuse_un_fichier_hors_resultats(self):
        hdr = [("content-type", "application/json")]

        async def go():
            ms = await self._started()
            st, _, _ = await _asgi(server.app, "POST", "/download", hdr, json.dumps({
                "search_id": ms.search_id, "username": "bon", "remote_path": "C:\\Windows\\secret.mp3"}).encode())
            st2, _, _ = await _asgi(server.app, "POST", "/download", hdr, json.dumps({
                "search_id": "inconnue", "username": "bon", "remote_path": "@@b\\Rips\\Artiste - Titre.flac"}).encode())
            return st, st2
        self.assertEqual(asyncio.run(go()), (404, 404))
        self.assertEqual(qm._jobs, {})

    def test_query_inconnue_404_et_champs_vides_422(self):
        async def go():
            a = (await _asgi(server.app, "GET", "/query/nope"))[0]
            b = (await _asgi(server.app, "POST", "/query", [("content-type", "application/json")], b'{"artist":" ","title":""}'))[0]
            return a, b
        self.assertEqual(asyncio.run(go()), (404, 422))


class Chosen(Base):
    def _run(self, artist, title, dl):
        downloader.download = dl
        c = SimpleNamespace(username="p", remote_path="@@p\\x\\Orig Name.mp3", filename="Orig Name.mp3",
                            fmt="mp3", bitrate=320, size=10, duration=0, free_slot=True)

        async def go():
            job = qm.create_chosen_job(c, artist, title)
            self.assertEqual(job.status, JobStatus.DOWNLOADING)
            for _ in range(10):
                await asyncio.sleep(0)
            return job
        return asyncio.run(go())

    def test_echec_du_fichier_choisi_ne_va_pas_en_liste_d_attente(self):
        async def boom(client, c, on_transfer=None):
            raise RuntimeError("pair déconnecté")
        job = self._run("Artiste", "Titre", boom)
        self.assertEqual((job.status, job.reason, job.error), (JobStatus.FAILED, "echec_choisi", "pair déconnecté"))
        self.assertEqual(wishlist.count(), 0)

    def test_sans_titre_le_fichier_garde_son_nom(self):
        async def ok(client, c, on_transfer=None):
            p = os.path.join(self.folder, c.filename)
            open(p, "wb").close()
            return p
        job = self._run("Artiste", "", ok)
        self.assertEqual(job.status, JobStatus.DONE)
        self.assertEqual(os.path.basename(job.file_path), "Orig Name.mp3")
        self.assertEqual(job.title, "Orig Name")


if __name__ == "__main__":
    unittest.main()
