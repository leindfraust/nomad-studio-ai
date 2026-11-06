"""Durable jobs: atomic snapshots, claims, resume without re-billing, failure."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nomadguard.contracts import (
    JobRecord,
    JobState,
    ProviderTaskRef,
    ShotRecord,
    ShotState,
    Submission,
    VideoRequest,
    VideoResult,
    utcnow,
)
from nomadguard.errors import InsufficientBalance, JobBusy
from nomadguard.jobs.runner import JobRunner
from nomadguard.jobs.store import JobStore
from nomadguard.providers.base import ProviderSet
from nomadguard.providers.offline import (
    OfflineImageProvider,
    OfflineTextProvider,
    OfflineVideoProvider,
    OfflineVisionProvider,
    OfflineVoiceProvider,
)

from .support import brief, make_settings, make_storyboard


def offline_set(settings) -> ProviderSet:
    return ProviderSet(
        text=OfflineTextProvider(),
        vision=OfflineVisionProvider(),
        image=OfflineImageProvider(settings.paths.ffmpeg),
        video=OfflineVideoProvider(settings.paths.ffmpeg, settings.paths.font_path),
        voice=OfflineVoiceProvider(settings.paths.ffmpeg),
    )


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = JobStore(self.tmp / "jobs")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_is_atomic_and_revisioned(self) -> None:
        record = self.store.create(brief())
        first = record.revision
        record.substep = "strategy"
        self.store.save(record)
        reloaded = self.store.get(record.id)
        self.assertGreater(reloaded.revision, first)
        self.assertEqual(reloaded.substep, "strategy")
        self.assertEqual([job.id for job in self.store.list()], [record.id])

    def test_unknown_job_is_a_missing_file_not_a_blank_record(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.store.get("2026-01-01-000000-nope")

    def test_claim_is_exclusive_then_released(self) -> None:
        record = self.store.create(brief())
        self.store.claim(record.id)
        with self.assertRaises(JobBusy):
            self.store.claim(record.id)
        self.store.release(record.id)
        self.store.claim(record.id)
        self.store.release(record.id)

    def test_stale_claim_from_a_dead_process_is_reclaimed(self) -> None:
        record = self.store.create(brief())
        (self.tmp / "jobs" / record.id / "run.lock").write_text("999999", encoding="ascii")
        self.store.claim(record.id)  # pid does not exist, so the claim is stale
        self.store.release(record.id)

    def test_artifact_path_traversal_is_refused(self) -> None:
        record = self.store.create(brief())
        with self.assertRaises(PermissionError):
            self.store.resolve_artifact(record.id, "../../secret.txt")
        inside = self.store.resolve_artifact(record.id, "final.mp4")
        self.assertTrue(str(inside).startswith(str(self.tmp)))

    def test_stalled_flag_only_for_live_jobs(self) -> None:
        record = self.store.create(brief())
        record.updated_at = utcnow().replace(year=2020)
        self.assertTrue(self.store.is_stalled(record, 60))
        record.state = JobState.FINALIZED
        self.assertFalse(self.store.is_stalled(record, 60))

    def test_a_reader_does_not_break_atomic_writes(self) -> None:
        """Windows refuses os.replace while another handle holds the file.

        The SSE stream reads job.json about once a second, so this race is the normal
        case during a live run, not an edge case. Reproduced here with a hot reader.
        """
        import threading

        record = self.store.create(brief())
        errors: list[BaseException] = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    self.store.get(record.id)
                except BaseException as exc:  # noqa: BLE001 - recorded, then asserted empty
                    errors.append(exc)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            for index in range(80):
                record.substep = f"step-{index}"
                self.store.save(record)
        finally:
            stop.set()
            thread.join(timeout=5)

        self.assertEqual(errors, [], f"reader hit {errors[:1]}")
        self.assertEqual(self.store.get(record.id).substep, "step-79")


class CountingProvider:
    """A video provider that records calls, so resume behaviour is measurable."""

    name = "counting"
    model = "counting"

    def __init__(self, *, fail_create: bool = False) -> None:
        self.creates = 0
        self.statuses = 0
        self.downloads = 0
        self.fail_create = fail_create

    def capabilities(self):
        from nomadguard.contracts import VideoCapabilities

        return VideoCapabilities(
            provider=self.name,
            model=self.model,
            durations_s=list(range(4, 16)),
            ratios=["9:16"],
            resolutions=["480P"],
            supports_first_frame=False,
            supports_reference_image=False,
            max_reference_images=0,
            price_per_second_usd={"480P": 0.025},
            source_url="test://counting",
            verified=False,
        )

    def render_prompt(self, intent):
        return f"{intent.subject} {intent.action}"

    def generate(self, request: VideoRequest) -> Submission:
        self.creates += 1
        if self.fail_create:
            raise InsufficientBalance("insufficient balance", status_code=402)
        return Submission(
            provider=self.name, model=self.model, task_id=f"t{self.creates}",
            submitted_at=utcnow(), poll_hint_s=0,
        )

    def get_status(self, task):
        self.statuses += 1
        from nomadguard.contracts import VideoJobStatus

        return VideoJobStatus(task_id=task.task_id, state=ShotState.DOWNLOADED, raw_state="succeeded")

    def get_result(self, task, destination: Path) -> VideoResult:
        self.downloads += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-bytes")
        return VideoResult(
            task_id=task.task_id, local_path=str(destination), provider_url="test://video"
        )


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.settings = make_settings(self.tmp)
        self.store = JobStore(self.settings.paths.jobs_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _record_with_storyboard(self, durations=None) -> JobRecord:
        record = self.store.create(brief())
        record.storyboard = make_storyboard(durations or [4, 4, 4])
        record.shots = [ShotRecord(index=scene.index) for scene in record.storyboard.scenes]
        record.state = JobState.GENERATING
        self.store.save(record)
        return record

    def _runner(self, video, *, text=None, vision=None, image=None, voice=None) -> JobRunner:
        providers = ProviderSet(
            text=text or OfflineTextProvider(),
            vision=vision or OfflineVisionProvider(),
            image=image or OfflineImageProvider(self.settings.paths.ffmpeg),
            video=video,
            voice=voice if voice is not None else OfflineVoiceProvider(self.settings.paths.ffmpeg),
        )
        return JobRunner(self.store, providers, self.settings, sleep=lambda _seconds: None)

    def test_resume_polls_an_in_flight_task_instead_of_paying_again(self) -> None:
        record = self._record_with_storyboard([10])
        provider = CountingProvider()
        record.shots[0].task = ProviderTaskRef(provider="counting", model="counting", task_id="t-existing")
        record.shots[0].state = ShotState.RUNNING
        self.store.save(record)

        self._runner(provider)._shot(record, record.storyboard.scenes[0], record.shots[0])

        self.assertEqual(provider.creates, 0, "resume must not submit a second paid task")
        self.assertEqual(provider.statuses, 1)
        self.assertEqual(provider.downloads, 1)
        self.assertEqual(record.shots[0].local_path, str(self.tmp / "jobs" / record.id / "artifacts" / "shot-0.mp4"))

    def test_missing_task_creates_once_and_estimates_cost(self) -> None:
        record = self._record_with_storyboard([10])
        provider = CountingProvider()
        self._runner(provider)._shot(record, record.storyboard.scenes[0], record.shots[0])
        self.assertEqual(provider.creates, 1)
        self.assertEqual(record.shots[0].state, ShotState.DOWNLOADED)
        self.assertAlmostEqual(record.costs.estimated_usd, 0.25, places=6)

    def test_permanent_provider_failure_fails_the_job_with_detail(self) -> None:
        record = self._record_with_storyboard([10])
        runner = self._runner(CountingProvider(fail_create=True))
        result = runner.run(record.id)
        self.assertEqual(result.state, JobState.FAILED)
        self.assertEqual(result.error["class"], "InsufficientBalance")
        self.assertIn("insufficient balance", str(result.error["message"]))
        self.assertEqual(result.shots[0].state, ShotState.PENDING)

    def test_failed_task_is_not_regenerated_automatically(self) -> None:
        class FailingStatus(CountingProvider):
            def get_status(self, task):
                self.statuses += 1
                from nomadguard.contracts import VideoJobStatus

                return VideoJobStatus(
                    task_id=task.task_id, state=ShotState.FAILED, raw_state="failed",
                    message="safety filter",
                )

        record = self._record_with_storyboard([10])
        record.shots[0].task = ProviderTaskRef(provider="counting", model="counting", task_id="t1")
        record.shots[0].state = ShotState.RUNNING
        self.store.save(record)
        provider = FailingStatus()
        result = self._runner(provider).run(record.id)
        self.assertEqual(result.state, JobState.FAILED)
        self.assertEqual(provider.creates, 0, "a failed paid task is never silently resubmitted")
        self.assertIn("safety filter", str(result.error["message"]))

    def test_offline_end_to_end_finalizes_with_a_real_file(self) -> None:
        record = self.store.create(brief())
        runner = self._runner(OfflineVideoProvider(self.settings.paths.ffmpeg, self.settings.paths.font_path))
        result = runner.run(record.id)

        self.assertEqual(result.state, JobState.FINALIZED, result.error or result.qa)
        self.assertTrue(result.placeholder)
        final = self.tmp / "jobs" / record.id / "artifacts" / "final.mp4"
        self.assertTrue(final.is_file())
        self.assertGreater(final.stat().st_size, 1000)
        self.assertIsNotNone(result.qa)
        self.assertTrue(result.qa.technical.valid, result.qa.technical.failures)
        self.assertEqual(len(result.shots), 3)
        self.assertIsNotNone(result.voiceover)
        self.assertTrue(result.qa.technical.has_audio, "the mixed file must carry the voice track")
        self.assertIn("NomadGuard", result.voiceover.text)

    def test_resume_re_enters_a_failed_job_and_keeps_its_artifacts(self) -> None:
        class CountingOffline(OfflineVideoProvider):
            creates = 0

            def generate(self, request):
                type(self).creates += 1
                return super().generate(request)

        record = self._record_with_storyboard([4, 4, 4])
        record.state = JobState.FAILED
        record.substep = "failed"
        record.error = {"class": "InsufficientBalance", "message": "no balance"}
        self.store.save(record)
        provider = CountingOffline(self.settings.paths.ffmpeg, self.settings.paths.font_path)

        result = self._runner(provider).run(record.id, retry_failed=True)

        self.assertEqual(result.state, JobState.FINALIZED, result.error)
        self.assertIsNone(result.error)
        self.assertEqual(CountingOffline.creates, 3, "resume must generate every pending shot")
        self.assertEqual(len(result.shots), 3)

    def test_run_without_retry_failed_leaves_a_failed_job_alone(self) -> None:
        record = self._record_with_storyboard([10])
        record.state = JobState.FAILED
        self.store.save(record)
        provider = CountingProvider()

        result = self._runner(provider).run(record.id)

        self.assertEqual(result.state, JobState.FAILED)
        self.assertEqual(provider.creates, 0)

    def test_later_shots_use_the_hero_frame_as_a_reference_not_a_first_frame(self) -> None:
        class Recording(CountingProvider):
            def __init__(self) -> None:
                super().__init__()
                self.requests: list[VideoRequest] = []

            def capabilities(self):
                caps = super().capabilities()
                return caps.model_copy(
                    update={"supports_first_frame": True, "supports_reference_image": True}
                )

            def generate(self, request: VideoRequest) -> Submission:
                self.requests.append(request)
                return super().generate(request)

        record = self._record_with_storyboard([4, 4, 4])
        hero = self.store.artifact_dir(record.id) / "keyframes" / "scene-0.png"
        hero.parent.mkdir(parents=True, exist_ok=True)
        hero.write_bytes(b"\x89PNG\r\n\x1a\n")
        for shot in record.shots:
            shot.keyframe_path = str(hero)
        self.store.save(record)
        provider = Recording()

        runner = self._runner(provider)
        for scene, shot in zip(record.storyboard.scenes, record.shots):
            runner._shot(record, scene, shot)

        self.assertEqual(len(provider.requests), 3)
        self.assertIsNotNone(provider.requests[0].first_frame_url, "shot 0 is composed from its frame")
        self.assertEqual(provider.requests[0].reference_image_urls, [])
        for later in provider.requests[1:]:
            self.assertIsNone(later.first_frame_url, "later shots must not be frozen to the hero pose")
            self.assertEqual(len(later.reference_image_urls), 1)

    def test_dry_run_stops_before_generation(self) -> None:
        record = self.store.create(brief(), dry_run=True)
        provider = CountingProvider()
        result = self._runner(provider).run(record.id)
        self.assertEqual(result.state, JobState.PLANNING)
        self.assertEqual(result.substep, "planned")
        self.assertIsNotNone(result.strategy)
        self.assertIsNotNone(result.storyboard)
        self.assertEqual(provider.creates, 0)


if __name__ == "__main__":
    unittest.main()
