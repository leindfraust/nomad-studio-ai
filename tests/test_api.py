"""HTTP surface: 202 semantics, conflict, containment, SPA routing, no secrets."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from nomadguard.contracts import JobRecord, JobState
from nomadguard.jobs.store import JobStore
from nomadguard.web.server import create_app

from .support import brief_dict, make_settings


class FakeRunner:
    """Records submissions instead of spending money."""

    def __init__(self, store: JobStore) -> None:
        self.store = store
        self.calls: list[str] = []

    def run(self, job_id: str, *, claimed: bool = False, retry_failed: bool = False) -> JobRecord:
        self.calls.append(job_id)
        record = self.store.get(job_id)
        if claimed:
            self.store.release(job_id)
        return record


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.settings = make_settings(self.tmp)
        self.store = JobStore(self.settings.paths.jobs_dir)
        self.runner = FakeRunner(self.store)
        self.dist = self.tmp / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<html><body>studio</body></html>", encoding="utf-8")
        (self.dist / "assets").mkdir()
        (self.dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        self.app = create_app(self.settings, runner=self.runner, dist=self.dist)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_job_returns_202_and_an_id(self) -> None:
        response = self.client.post("/api/jobs", json={"brief": brief_dict()})
        self.assertEqual(response.status_code, 202)
        job_id = response.json()["id"]
        self.assertTrue(self.store.exists(job_id))
        self.assertEqual(self.runner.calls, [job_id])

    def test_invalid_brief_is_422(self) -> None:
        payload = {"brief": {**brief_dict(), "duration_s": 42}}
        self.assertEqual(self.client.post("/api/jobs", json=payload).status_code, 422)

    def test_extra_keys_are_rejected(self) -> None:
        payload = {"brief": {**brief_dict(), "surprise": True}}
        self.assertEqual(self.client.post("/api/jobs", json=payload).status_code, 422)

    def test_unknown_job_is_json_404(self) -> None:
        response = self.client.get("/api/jobs/2026-01-01-000000-nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("application/json", response.headers["content-type"])

    def test_unknown_api_path_is_json_404_not_the_spa(self) -> None:
        response = self.client.get("/api/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertIn("application/json", response.headers["content-type"])

    def test_producing_a_planned_job_keeps_its_planning_artifacts(self) -> None:
        from nomadguard.contracts import Brief

        planned = self.store.create(Brief(**brief_dict()), dry_run=True)
        response = self.client.post(f"/api/jobs/{planned.id}/produce")
        self.assertEqual(response.status_code, 202)
        refreshed = self.store.get(planned.id)
        self.assertFalse(refreshed.dry_run)
        self.assertEqual(self.runner.calls, [planned.id])

    def test_produce_unknown_job_is_404(self) -> None:
        self.assertEqual(self.client.post("/api/jobs/nope/produce").status_code, 404)

    def test_resume_conflict_is_409(self) -> None:
        job_id = self.store.create(self.store_brief()).id
        self.store.claim(job_id)
        response = self.client.post(f"/api/jobs/{job_id}/resume")
        self.assertEqual(response.status_code, 409)
        self.store.release(job_id)

    def test_public_providers_payload_has_no_credentials(self) -> None:
        response = self.client.get("/api/providers")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("test-key", response.text)
        self.assertEqual(response.json()["video"]["model"], "MiniMax-H3-Lite")

    def test_artifact_traversal_is_refused(self) -> None:
        job_id = self.store.create(self.store_brief()).id
        for attempt in ("../../job.json", "..%2F..%2Fjob.json"):
            response = self.client.get(f"/api/jobs/{job_id}/files/{attempt}")
            self.assertIn(response.status_code, (403, 404))

    def test_artifact_inside_the_job_is_served(self) -> None:
        job_id = self.store.create(self.store_brief()).id
        target = self.store.artifact_dir(job_id) / "final.mp4"
        target.write_bytes(b"not-really-a-video")
        response = self.client.get(f"/api/jobs/{job_id}/files/final.mp4")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"not-really-a-video")

    def test_brief_round_trip(self) -> None:
        put = self.client.put("/api/brief", json=brief_dict())
        self.assertEqual(put.status_code, 200)
        self.assertEqual(self.client.get("/api/brief").json()["brand"], "NomadGuard")
        self.assertTrue(self.settings.paths.brief_path.is_file())

    def test_spa_serves_index_and_assets(self) -> None:
        root = self.client.get("/")
        self.assertIn("studio", root.text)
        asset = self.client.get("/assets/app.js")
        self.assertEqual(asset.status_code, 200)
        deep_link = self.client.get("/jobs/anything")
        self.assertIn("studio", deep_link.text)

    def test_cross_origin_mutation_is_refused(self) -> None:
        response = self.client.post(
            "/api/jobs",
            json={"brief": brief_dict()},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_loopback_origin_mutation_is_allowed(self) -> None:
        response = self.client.post(
            "/api/jobs",
            json={"brief": brief_dict()},
            headers={"Origin": "http://localhost:5173"},
        )
        self.assertEqual(response.status_code, 202)

    def test_events_stream_ends_on_terminal_state(self) -> None:
        job_id = self.store.create(self.store_brief()).id
        record = self.store.get(job_id)
        record.state = JobState.FINALIZED
        record.substep = "done"
        self.store.save(record)
        with self.client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
            body = "".join(stream.iter_text())
        self.assertIn("FINALIZED", body)

    def store_brief(self):
        from nomadguard.contracts import Brief

        return Brief(**brief_dict())


if __name__ == "__main__":
    unittest.main()
