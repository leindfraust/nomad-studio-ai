"""Boundary contracts and configuration: the shapes every other module trusts."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from nomadguard.config import load_settings
from nomadguard.contracts import JobRecord
from nomadguard.errors import ConfigError, CapabilityMismatch

from .support import brief, make_settings, scene_intent, valid_env, video_provider, FakeTransport


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unknown_provider_is_rejected_not_substituted(self) -> None:
        env = valid_env(self.tmp)
        env["NOMADGUARD_VIDEO_PROVIDER"] = "gemini"
        with self.assertRaises(ConfigError) as ctx:
            load_settings(env)
        self.assertIn("gemini", str(ctx.exception))
        self.assertIn("not implemented", str(ctx.exception))

    def test_all_problems_are_reported_together(self) -> None:
        env = valid_env(self.tmp)
        env.pop("MINIMAX_API_KEY")
        env["NOMADGUARD_QA_MIN_MEAN"] = "not-a-number"
        with self.assertRaises(ConfigError):
            load_settings(env)

    def test_missing_credentials_named(self) -> None:
        env = valid_env(self.tmp)
        env.pop("MINIMAX_API_KEY")
        with self.assertRaises(ConfigError) as ctx:
            load_settings(env)
        self.assertIn("MINIMAX_API_KEY", str(ctx.exception))

    def test_capabilities_bind_independently(self) -> None:
        env = valid_env(self.tmp)
        env["NOMADGUARD_VISION_PROVIDER"] = "openai_compat"
        env["NOMADGUARD_VISION_MODEL"] = "some-vision-model"
        env["OPENAI_COMPAT_BASE_URL"] = "https://example.invalid/v1"
        env["OPENAI_COMPAT_API_KEY"] = "compat-key"
        settings = load_settings(env)
        self.assertEqual(settings.text.provider, "minimax")
        self.assertEqual(settings.vision.provider, "openai_compat")
        self.assertEqual(settings.vision.model, "some-vision-model")

    def test_offline_providers_are_refused_without_the_flag(self) -> None:
        from nomadguard.config import load_settings
        from nomadguard.providers.factory import build_providers

        env = valid_env(self.tmp)
        env["NOMADGUARD_VIDEO_PROVIDER"] = "offline"
        env["NOMADGUARD_VIDEO_MODEL"] = "offline-video"
        settings = load_settings(env)
        with self.assertRaises(ConfigError) as ctx:
            build_providers(settings)
        self.assertIn("NOMADGUARD_ALLOW_PLACEHOLDER", str(ctx.exception))

    def test_offline_providers_are_allowed_with_the_flag(self) -> None:
        from nomadguard.config import load_settings
        from nomadguard.providers.factory import build_providers

        env = valid_env(self.tmp)
        env["NOMADGUARD_VIDEO_PROVIDER"] = "offline"
        env["NOMADGUARD_VIDEO_MODEL"] = "offline-video"
        env["NOMADGUARD_ALLOW_PLACEHOLDER"] = "1"
        build_providers(load_settings(env))

    def test_env_file_overrides_an_inherited_variable(self) -> None:
        from nomadguard.config import load_env_file

        path = self.tmp / ".env"
        path.write_text("NOMADGUARD_VIDEO_PROVIDER=minimax\n", encoding="utf-8")
        os.environ["NOMADGUARD_VIDEO_PROVIDER"] = "offline"
        try:
            load_env_file(path)
            self.assertEqual(os.environ["NOMADGUARD_VIDEO_PROVIDER"], "minimax")
        finally:
            os.environ.pop("NOMADGUARD_VIDEO_PROVIDER", None)

    def test_public_config_contains_no_credentials(self) -> None:
        public = json.dumps(make_settings(self.tmp).public())
        self.assertNotIn("test-key", public)
        self.assertNotIn("api_key", public.lower())


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unknown_keys_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            JobRecord(id="job-1", brief=brief(), surprise="field")

    def test_unknown_enum_value_raises(self) -> None:
        with self.assertRaises(ValidationError):
            JobRecord(id="job-1", brief=brief(), state="DONE")

    def test_brief_duration_outside_the_brief_is_rejected(self) -> None:
        payload = {**brief().model_dump(), "duration_s": 9}
        with self.assertRaises(ValidationError):
            JobRecord(id="job-1", brief=payload)

    def test_incompatible_resolution_is_capability_mismatch(self) -> None:
        provider = video_provider(FakeTransport())
        caps = provider.capabilities()
        self.assertNotIn("2K", caps.resolutions)
        with self.assertRaises(CapabilityMismatch):
            provider._validate_resolution("2K")  # type: ignore[attr-defined]

    def test_capabilities_declare_nine_by_sixteen_and_source(self) -> None:
        caps = video_provider(FakeTransport()).capabilities()
        self.assertIn("9:16", caps.ratios)
        self.assertEqual(caps.durations_s[0], 4)
        self.assertEqual(caps.durations_s[-1], 15)
        self.assertIn("pricing", caps.source_url)
        self.assertFalse(caps.verified)  # unproven until the first live call

    def test_prompt_renderer_carries_no_copy(self) -> None:
        rendered = video_provider(FakeTransport()).render_prompt(scene_intent())
        self.assertIn("slow push in", rendered)
        self.assertIn("no on-screen text", rendered.lower())
        self.assertIn("no readable screens", rendered.lower())


if __name__ == "__main__":
    unittest.main()
