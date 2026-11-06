"""Provider adapters: wire mapping, error classification, ambiguity, downloads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nomadguard.contracts import ProviderTaskRef
from nomadguard.errors import (
    ConfigError,
    InsufficientBalance,
    InvalidRequest,
    ProviderError,
    RateLimited,
    SafetyRefusal,
    SubmissionUnknown,
    TaskFailed,
)
from nomadguard.providers.minimax import MinimaxHttp, MinimaxImageProvider, MinimaxTextProvider

from .support import FakeTransport, task_ref, video_provider, video_request

SUCCEEDED = ("task", {"status": "succeeded", "content": {"url": "https://cdn.example/v.mp4"}})


class VideoAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_returns_a_task_reference(self) -> None:
        transport = FakeTransport((200, {"task_id": "abc123"}))
        submission = video_provider(transport).generate(video_request())
        self.assertEqual(submission.task_id, "abc123")
        call = transport.calls[0]
        self.assertEqual(call["body"]["ratio"], "9:16")
        self.assertEqual(call["body"]["duration"], 4)
        self.assertNotIn("Authorization", str(call["body"]))

    def test_status_states_are_normalised(self) -> None:
        for raw, expected in (
            ("queued", "queued"),
            ("running", "running"),
            ("succeeded", "downloaded"),
        ):
            transport = FakeTransport((200, {"task": {"status": raw}}))
            status = video_provider(transport).get_status(task_ref())
            self.assertEqual(status.raw_state, raw)
            self.assertEqual(status.state.value, expected)

    def test_unknown_status_raises_instead_of_guessing(self) -> None:
        transport = FakeTransport((200, {"task": {"status": "teleported"}}))
        with self.assertRaises(ProviderError):
            video_provider(transport).get_status(task_ref())

    def test_payment_failure_is_not_retryable(self) -> None:
        transport = FakeTransport((402, {"error": {"message": "insufficient balance"}, "request_id": "r1"}))
        with self.assertRaises(InsufficientBalance) as ctx:
            video_provider(transport).generate(video_request())
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(ctx.exception.request_id, "r1")

    def test_rate_limit_is_retryable_with_provider_delay(self) -> None:
        transport = FakeTransport(
            (429, {"error": {"message": "slow down"}}, {"Retry-After": "7"})
        )
        with self.assertRaises(RateLimited) as ctx:
            video_provider(transport).generate(video_request())
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.retry_after_s, 7.0)

    def test_auth_and_safety_failures_are_not_retryable(self) -> None:
        for status, expected in ((401, InvalidRequest), (422, SafetyRefusal)):
            transport = FakeTransport((status, {"error": {"message": "no"}}))
            with self.assertRaises(expected) as ctx:
                video_provider(transport).generate(video_request())
            self.assertFalse(ctx.exception.retryable)

    def test_transport_failure_on_create_is_unknown_not_retried(self) -> None:
        transport = FakeTransport(OSError("connection reset"))
        with self.assertRaises(SubmissionUnknown) as ctx:
            video_provider(transport).generate(video_request())
        self.assertFalse(ctx.exception.retryable)

    def test_success_without_a_task_id_is_ambiguous(self) -> None:
        transport = FakeTransport((200, {"unexpected": True}))
        with self.assertRaises(SubmissionUnknown):
            video_provider(transport).generate(video_request())

    def test_result_before_success_raises(self) -> None:
        transport = FakeTransport((200, {"task": {"status": "running"}}))
        with self.assertRaises(ProviderError):
            video_provider(transport).get_result(task_ref(), self.tmp / "shot.mp4")

    def test_failed_task_reports_provider_detail(self) -> None:
        transport = FakeTransport(
            (200, {"task": {"status": "failed", "error": {"message": "safety filter"}}})
        )
        with self.assertRaises(TaskFailed) as ctx:
            video_provider(transport).get_result(task_ref(), self.tmp / "shot.mp4")
        self.assertIn("safety filter", str(ctx.exception))

    def test_result_downloads_to_disk(self) -> None:
        transport = FakeTransport(
            (200, {"task": {"status": "succeeded", "content": {"url": "https://cdn.example/v.mp4"},
                            "duration": 4, "resolution": "480P"}}),
            (200, b"fake-mp4-bytes"),
        )
        destination = self.tmp / "nested" / "shot-0.mp4"
        result = video_provider(transport).get_result(task_ref(), destination)
        self.assertEqual(destination.read_bytes(), b"fake-mp4-bytes")
        self.assertEqual(result.local_path, str(destination))
        self.assertEqual(transport.calls[1]["method"], "GET")

    def test_first_frame_and_reference_images_are_mutually_exclusive(self) -> None:
        transport = FakeTransport()
        request = video_request(
            first_frame_url="data:image/png;base64,AAA",
            reference_image_urls=["data:image/png;base64,BBB"],
        )
        with self.assertRaises(InvalidRequest):
            video_provider(transport).generate(request)
        self.assertEqual(transport.calls, [])

    def test_unsupported_resolution_never_reaches_the_network(self) -> None:
        transport = FakeTransport()
        with self.assertRaises(Exception):
            video_provider(transport).generate(video_request(resolution="2K"))
        self.assertEqual(transport.calls, [])


class ModelCatalogTests(unittest.TestCase):
    def test_unknown_text_model_fails_at_construction(self) -> None:
        with self.assertRaises(ConfigError):
            MinimaxTextProvider(MinimaxHttp("k", transport=FakeTransport()), "gpt-nope")

    def test_unknown_image_model_fails_at_construction(self) -> None:
        with self.assertRaises(ConfigError):
            MinimaxImageProvider(MinimaxHttp("k", transport=FakeTransport()), "image-99")

    def test_task_reference_pins_provider_and_model(self) -> None:
        ref = ProviderTaskRef(provider="minimax", model="MiniMax-H3-Lite", task_id="t")
        self.assertEqual(ref.model, "MiniMax-H3-Lite")


if __name__ == "__main__":
    unittest.main()
