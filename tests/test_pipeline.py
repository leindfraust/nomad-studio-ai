"""Creative pipeline: legal timing, contract enforcement, QA arithmetic."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from nomadguard.config import Policy
from nomadguard.contracts import CreativeReport, TextRequest, TextResult, VideoCapabilities
from nomadguard.errors import CapabilityMismatch, ConfigError, InsufficientBalance
from nomadguard.pipeline.qa_creative import CreativeQAError, evaluate as evaluate_creative, gate_reasons
from nomadguard.pipeline.storyboard import StoryboardError, build_storyboard, plan_shots
from nomadguard.pipeline.strategy import StrategyError, build_strategy

from .support import brief, make_settings, make_storyboard, scene_intent, video_provider, FakeTransport


def capabilities(durations: list[int], model: str = "test") -> VideoCapabilities:
    return VideoCapabilities(
        provider="test",
        model=model,
        durations_s=durations,
        ratios=["9:16"],
        resolutions=["480P"],
        supports_first_frame=False,
        supports_reference_image=False,
        max_reference_images=0,
        price_per_second_usd={"480P": 0.025},
        source_url="test://capabilities",
        verified=False,
    )


class PartitionTests(unittest.TestCase):
    def test_twelve_seconds_is_three_equal_shots(self) -> None:
        plan = plan_shots(12, capabilities(list(range(4, 16))))
        self.assertEqual(plan.durations, [4, 4, 4])
        self.assertIsNone(plan.reduced_from)

    def test_ten_seconds_cannot_have_three_h3_shots(self) -> None:
        plan = plan_shots(10, capabilities(list(range(4, 16))))
        self.assertEqual(plan.durations, [5, 5])
        self.assertEqual(plan.reduced_from, 3)

    def test_sparse_catalog_gives_a_different_cut(self) -> None:
        plan = plan_shots(10, capabilities([4, 6, 8]))
        self.assertEqual(plan.durations, [4, 6])

    def test_one_shot_catalog_cannot_express_twelve(self) -> None:
        with self.assertRaises(CapabilityMismatch):
            plan_shots(12, capabilities([8]))

    def test_every_partition_sums_and_is_legal(self) -> None:
        for target in range(10, 16):
            plan = plan_shots(target, capabilities(list(range(4, 16))))
            self.assertEqual(sum(plan.durations), target)
            self.assertTrue(all(4 <= duration <= 15 for duration in plan.durations))
            self.assertEqual(plan.durations, sorted(plan.durations), "hook should be the tightest cut")


class StrategyTests(unittest.TestCase):
    def test_reason_is_rewritten_when_it_argues_for_another_angle(self) -> None:
        from nomadguard.pipeline.strategy import build_strategy

        class Scripted2:
            name = "scripted"
            model = "scripted"

            def __init__(self) -> None:
                self.prompts: list[str] = []

            def generate(self, request: TextRequest) -> TextResult:
                self.prompts.append(request.user)
                if "TASK: ANGLES" in request.user:
                    payload = {
                        "angles": [
                            {"name": "A", "target_emotion": "calm", "hook": "h1", "concept": "c1", "cta": "cta"},
                            {"name": "B", "target_emotion": "cost", "hook": "h2", "concept": "c2", "cta": "cta"},
                            {"name": "C", "target_emotion": "play", "hook": "h3", "concept": "c3", "cta": "cta"},
                        ]
                    }
                elif "TASK: SCORES" in request.user:
                    payload = {
                        "scores": [
                            {"angle_name": "A", "insight_strength": 8, "hook_viability": 8,
                             "message_fidelity": 8, "reason": "r"},
                            {"angle_name": "B", "insight_strength": 8, "hook_viability": 8,
                             "message_fidelity": 8, "reason": "r"},
                            {"angle_name": "C", "insight_strength": 4, "hook_viability": 4,
                             "message_fidelity": 4, "reason": "r"},
                        ],
                        "selection_reason": "B wins because it is the most concrete.",
                        "audience_insight": "i",
                    }
                else:
                    payload = {"selection_reason": "A wins on composure and clarity."}
                return TextResult(text=json.dumps(payload), model=self.model, cost_usd=0.0)

        provider = Scripted2()
        strategy, _ = build_strategy(brief(), provider)
        self.assertEqual(strategy.selected_name, "A")
        self.assertIn("A", strategy.selection_reason)
        self.assertEqual(sum("TASK: REASON" in prompt for prompt in provider.prompts), 1)

    def test_consistent_reason_costs_no_extra_call(self) -> None:
        from nomadguard.pipeline.strategy import build_strategy

        class Scripted3:
            name = "scripted"
            model = "scripted"

            def __init__(self) -> None:
                self.prompts: list[str] = []

            def generate(self, request: TextRequest) -> TextResult:
                self.prompts.append(request.user)
                if "TASK: ANGLES" in request.user:
                    payload = {"angles": [
                        {"name": "A", "target_emotion": "calm", "hook": "h1", "concept": "c1", "cta": "cta"},
                        {"name": "B", "target_emotion": "cost", "hook": "h2", "concept": "c2", "cta": "cta"},
                        {"name": "C", "target_emotion": "play", "hook": "h3", "concept": "c3", "cta": "cta"},
                    ]}
                else:
                    payload = {
                        "scores": [
                            {"angle_name": "A", "insight_strength": 9, "hook_viability": 9,
                             "message_fidelity": 9, "reason": "r"},
                            {"angle_name": "B", "insight_strength": 5, "hook_viability": 5,
                             "message_fidelity": 5, "reason": "r"},
                            {"angle_name": "C", "insight_strength": 4, "hook_viability": 4,
                             "message_fidelity": 4, "reason": "r"},
                        ],
                        "selection_reason": "A is the strongest bet: composure is the insight.",
                        "audience_insight": "i",
                    }
                return TextResult(text=json.dumps(payload), model=self.model, cost_usd=0.0)

        provider = Scripted3()
        strategy, _ = build_strategy(brief(), provider)
        self.assertEqual(strategy.selected_name, "A")
        self.assertEqual(sum("TASK: REASON" in prompt for prompt in provider.prompts), 0)

    class Scripted:
        name = "scripted"
        model = "scripted"

        def __init__(self, responses: dict[str, dict]) -> None:
            self.responses = responses
            self.prompts: list[str] = []

        def generate(self, request: TextRequest) -> TextResult:
            self.prompts.append(request.user)
            for marker, payload in self.responses.items():
                if marker in request.user:
                    return TextResult(text=json.dumps(payload), model=self.model, cost_usd=0.0)
            raise AssertionError(f"no scripted response for: {request.user[:80]}")

    def _angles(self) -> dict:
        return {
            "angles": [
                {"name": "A", "target_emotion": "relief", "hook": "h1", "concept": "c1", "cta": "cta"},
                {"name": "B", "target_emotion": "security", "hook": "h2", "concept": "c2", "cta": "cta"},
                {"name": "C", "target_emotion": "confidence", "hook": "h3", "concept": "c3", "cta": "cta"},
            ]
        }

    def _scores(self, a: int, b: int, c: int, b_hook: int = 5) -> dict:
        return {
            "scores": [
                {"angle_name": "A", "insight_strength": a, "hook_viability": 5, "message_fidelity": a, "reason": "r"},
                {"angle_name": "B", "insight_strength": b, "hook_viability": b_hook, "message_fidelity": b, "reason": "r"},
                {"angle_name": "C", "insight_strength": c, "hook_viability": 5, "message_fidelity": c, "reason": "r"},
            ],
            "selection_reason": "B wins on the tension the audience already feels.",
            "audience_insight": "Being stranded is a cost problem before it is an inconvenience.",
        }

    def test_winner_is_computed_from_scores(self) -> None:
        provider = self.Scripted({"TASK: ANGLES": self._angles(), "TASK: SCORES": self._scores(5, 9, 6)})
        strategy, cost = build_strategy(brief(), provider)
        self.assertEqual(strategy.selected_name, "B")
        self.assertEqual(cost, 0.0)
        self.assertEqual(len(strategy.scores), 3)

    def test_tie_breaks_on_hook_viability(self) -> None:
        provider = self.Scripted(
            {"TASK: ANGLES": self._angles(), "TASK: SCORES": self._scores(8, 8, 6, b_hook=9)}
        )
        strategy, _ = build_strategy(brief(), provider)
        self.assertEqual(strategy.selected_name, "B")

    def test_missing_angle_is_rejected(self) -> None:
        angles = self._angles()
        angles["angles"] = angles["angles"][:2]
        provider = self.Scripted({"TASK: ANGLES": angles, "TASK: SCORES": self._scores(5, 5, 5)})
        with self.assertRaises(StrategyError):
            build_strategy(brief(), provider)

    def test_score_must_cover_every_angle(self) -> None:
        scores = self._scores(5, 5, 5)
        scores["scores"] = scores["scores"][:2]
        provider = self.Scripted({"TASK: ANGLES": self._angles(), "TASK: SCORES": scores})
        with self.assertRaises(StrategyError):
            build_strategy(brief(), provider)


SCENE = {
    "beat": "hook",
    "description": "The traveller looks up as the board clears.",
    "camera": "slow push in",
    "on_screen_text": "Your flight just vanished.",
    "intent": scene_intent().model_dump(),
}


class StoryboardTests(unittest.TestCase):
    class Scripted:
        name = "scripted"
        model = "scripted"

        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def generate(self, request: TextRequest) -> TextResult:
            return TextResult(text=json.dumps(self.payload), model=self.model, cost_usd=0.01)

    def _payload(self, count: int = 3) -> dict:
        beats = ["hook", "tension", "relief"]
        return {
            "continuity_block": "charcoal coat, dusk terminal, cool blue and warm amber",
            "scenes": [{**SCENE, "beat": beats[index]} for index in range(count)],
        }

    def _strategy(self):
        from nomadguard.pipeline.strategy import StrategyError  # noqa: F401
        from nomadguard.contracts import Angle, AngleScore, Strategy

        angles = [Angle(name=n, target_emotion="relief", hook="h", concept="c", cta="cta") for n in ("A", "B", "C")]
        scores = [
            AngleScore(angle_name=n, insight_strength=8, hook_viability=8, message_fidelity=8, reason="r")
            for n in ("A", "B", "C")
        ]
        return Strategy(
            angles=angles, scores=scores, selected_name="A", selection_reason="r", audience_insight="i"
        )

    def test_builds_scenes_on_the_planned_timing(self) -> None:
        video = video_provider(FakeTransport())
        storyboard, cost = build_storyboard(
            brief(), self._strategy(), capabilities(list(range(4, 16))), video, self.Scripted(self._payload())
        )
        self.assertEqual([scene.duration_s for scene in storyboard.scenes], [4, 4, 4])
        self.assertEqual(storyboard.total_duration_s, 12)
        self.assertEqual(cost, 0.01)
        self.assertEqual(storyboard.scenes[0].start_s, 0.0)
        self.assertEqual(storyboard.scenes[2].start_s, 8.0)

    def test_scene_count_must_match_the_plan(self) -> None:
        video = video_provider(FakeTransport())
        with self.assertRaises(StoryboardError):
            build_storyboard(
                brief(), self._strategy(), capabilities(list(range(4, 16))), video, self.Scripted(self._payload(2))
            )

    def test_video_prompt_never_contains_the_copy(self) -> None:
        video = video_provider(FakeTransport())
        storyboard, _ = build_storyboard(
            brief(), self._strategy(), capabilities(list(range(4, 16))), video, self.Scripted(self._payload())
        )
        for scene in storyboard.scenes:
            self.assertNotIn("Your flight just vanished", scene.provider_prompt)
        self.assertEqual(storyboard.scenes[0].on_screen_text, "Your flight just vanished.")


def policy(**overrides) -> Policy:
    base = dict(
        retry_max_attempts=3,
        retry_backoff_s=0,
        poll_interval_s=0.01,
        staleness_s=600,
        qa_min_mean=7,
        qa_min_dimension=6,
        duration_tolerance_s=0.5,
        aspect_tolerance=0.03,
        resolution_floor_short_edge=480,
    )
    base.update(overrides)
    return Policy(**base)


class CreativeGateTests(unittest.TestCase):
    def _report(self, **scores: int) -> CreativeReport:
        base = {
            "realism": 8, "visual_consistency": 8, "brief_adherence": 9,
            "hook_strength": 7, "advertising_quality": 8, "storyboard_fidelity": 4,
        }
        base.update(scores)
        return CreativeReport(
            model="test", scores=base, justifications={}, defects=[], frames=[], passed=True
        )

    def test_storyboard_fidelity_does_not_gate(self) -> None:
        """The storyboard is a production plan; the client's bar is the brief."""
        report = self._report(storyboard_fidelity=1)
        self.assertEqual(gate_reasons(report, policy()), [])

    def test_strong_scores_produce_no_reasons(self) -> None:
        self.assertEqual(gate_reasons(self._report(), policy()), [])

    def test_low_mean_is_a_reason(self) -> None:
        reasons = gate_reasons(self._report(realism=5, hook_strength=5, advertising_quality=5), policy())
        self.assertTrue(any("mean" in reason for reason in reasons))

    def test_single_low_dimension_is_a_reason(self) -> None:
        reasons = gate_reasons(self._report(hook_strength=4), policy())
        self.assertTrue(any("hook_strength" in reason for reason in reasons))

    def test_vision_model_scores_are_validated(self) -> None:
        class Bad:
            name = "bad"
            model = "bad"

            def evaluate(self, request):
                return type(
                    "R", (), {"text": json.dumps({"scores": {"realism": 11}, "justifications": {}, "defects": []}), "model": "bad"}
                )()

        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "f.jpg"
            frame.write_bytes(b"\xff\xd8\xff")
            from .support import make_storyboard

            with self.assertRaises(CreativeQAError):
                evaluate_creative(
                    [frame], brief=brief(), storyboard=make_storyboard(), vision=Bad(), policy=policy()
                )

    def test_half_point_scores_are_accepted(self) -> None:
        """A 0-10 rubric may come back as 6.5; rejecting that wastes a paid run."""

        class Decimal:
            name = "decimal"
            model = "decimal"

            def evaluate(self, request):
                scores = {
                    "realism": 6.5, "visual_consistency": 7.0, "brief_adherence": 8,
                    "hook_strength": 7.5, "advertising_quality": 7, "storyboard_fidelity": 6,
                }
                return type(
                    "R", (),
                    {"text": json.dumps({"scores": scores, "justifications": {}, "defects": []}),
                     "model": "decimal", "cost_usd": 0.0},
                )()

        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "f.jpg"
            frame.write_bytes(b"\xff\xd8\xff")
            from .support import make_storyboard

            report, _cost = evaluate_creative(
                [frame], brief=brief(), storyboard=make_storyboard(), vision=Decimal(), policy=policy()
            )
            self.assertEqual(report.scores["realism"], 6.5)
            self.assertTrue(report.passed)


class JsonRepairTests(unittest.TestCase):
    """A model quote-slip must cost one small repair call, not the whole run."""

    class Scripted:
        name = "scripted"
        model = "scripted"

        def __init__(self, replies: list[str]) -> None:
            self.replies = replies
            self.calls = 0

        def generate(self, request: TextRequest) -> TextResult:
            self.calls += 1
            return TextResult(text=self.replies[min(self.calls - 1, len(self.replies) - 1)],
                              model=self.model, cost_usd=0.001)

    def test_repair_call_recovers_valid_json(self) -> None:
        from nomadguard.pipeline.llmjson import parse_with_repair

        provider = self.Scripted(['{"a": 1}', '{"a": 3}'])
        payload, cost = parse_with_repair('{"a": "unterminated}', provider)
        self.assertEqual(payload, {"a": 1})
        self.assertEqual(provider.calls, 1)
        self.assertAlmostEqual(cost, 0.001)

    def test_unrepairable_output_keeps_the_raw_text(self) -> None:
        from nomadguard.pipeline.llmjson import ModelOutputError, parse_with_repair

        provider = self.Scripted(['still not json'])
        with self.assertRaises(ModelOutputError) as ctx:
            parse_with_repair('{"a": "unterminated}', provider)
        self.assertIn("unterminated", ctx.exception.raw or "")

    def test_no_provider_means_no_repair_attempt(self) -> None:
        from nomadguard.pipeline.llmjson import ModelOutputError, parse_with_repair

        with self.assertRaises(ModelOutputError):
            parse_with_repair('{"a": }', None)


class RatioLabelTests(unittest.TestCase):
    def test_vertical_canvas_is_labelled_by_its_intended_ratio(self) -> None:
        from nomadguard.pipeline.qa_technical import _ratio_label

        self.assertEqual(_ratio_label(480, 854, 0.03), "9:16")

    def test_square_is_square(self) -> None:
        from nomadguard.pipeline.qa_technical import _ratio_label

        self.assertEqual(_ratio_label(480, 480, 0.03), "1:1")

    def test_unusual_ratio_keeps_its_exact_form(self) -> None:
        from nomadguard.pipeline.qa_technical import _ratio_label

        self.assertEqual(_ratio_label(1000, 700, 0.03), "10:7")


class TypographyTests(unittest.TestCase):
    def test_long_copy_wraps_instead_of_overflowing(self) -> None:
        from nomadguard.pipeline.assemble import wrap_text

        lines = wrap_text("Travel protected with NomadGuard.", 20)
        self.assertEqual(lines, ["Travel protected", "with NomadGuard."])
        self.assertTrue(all(len(line) <= 20 for line in lines))

    def test_font_shrinks_to_fit_the_frame(self) -> None:
        from nomadguard.pipeline.assemble import fit_font_size

        narrow = fit_font_size(480, ["Travel protected with NomadGuard."], 35)
        self.assertLess(narrow, 35)
        self.assertLessEqual(480 * 0.86, 480 * 0.86)  # sanity on the budget
        self.assertLessEqual(len("Travel protected with NomadGuard.") * 0.56 * narrow, 480 * 0.86 + 1)

    def test_short_copy_keeps_the_base_size(self) -> None:
        from nomadguard.pipeline.assemble import fit_font_size

        self.assertEqual(fit_font_size(480, ["NomadGuard"], 35), 35)


    def test_product_plate_targets_the_tension_shot(self) -> None:
        import tempfile
        from nomadguard.pipeline.assemble import _chip_filters

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            filters = _chip_filters(
                make_storyboard([4, 4, 4]), 768, 1344, "font.ttf", "symbol.ttf", "NomadGuard", work
            )
            joined = ",".join(filters)
            self.assertIn("drawbox", joined)
            self.assertIn("between(t,4.20,7.80)", joined)
            self.assertIn("fontfile=symbol.ttf", joined)
            self.assertIn("chip-brand.txt", joined)
            self.assertEqual((work / "chip-check.txt").read_text(encoding="utf-8"), "\u2713")

    def test_plate_is_text_only_without_a_symbol_font(self) -> None:
        import tempfile
        from nomadguard.pipeline.assemble import _chip_filters

        with tempfile.TemporaryDirectory() as tmp:
            filters = _chip_filters(
                make_storyboard([4, 4, 4]), 768, 1344, "font.ttf", None, "NomadGuard", Path(tmp)
            )
            joined = ",".join(filters)
            self.assertNotIn("chip-check.txt", joined)
            self.assertIn("chip-brand.txt", joined)

    def test_end_card_follows_the_actual_cut_length(self) -> None:
        import tempfile
        from nomadguard.pipeline.assemble import _overlay_filters

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            filters = _overlay_filters(
                make_storyboard([4, 4, 4]), 480, 854, "font.ttf", "NomadGuard",
                "Travel protected with NomadGuard.", work, 13.4,
            )
            card = [item for item in filters if "endcard.txt" in item][0]
            self.assertIn("gte(t,11.40)", card)
            self.assertNotIn("gte(t,10.00)", card)


class AssemblyTests(unittest.TestCase):
    """ffmpeg runs with cwd=work_dir, so relative job paths are the real risk."""

    def test_assembly_works_from_a_relative_working_directory(self) -> None:
        import os
        import subprocess
        from nomadguard.pipeline.assemble import assemble

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.chdir(root)
            try:
                for index in range(2):
                    subprocess.run(
                        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                         "-i", f"color=c=0x101820:s=480x854:r=24:d=5",
                         "-c:v", "libx264", "-pix_fmt", "yuv420p", f"shot-{index}.mp4"],
                        check=True, capture_output=True,
                    )
                storyboard = make_storyboard([5, 5])
                output = assemble(
                    storyboard,
                    [Path("shot-0.mp4"), Path("shot-1.mp4")],
                    Path("final.mp4"),
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                    font_path=_font_path(),
                    brand="NomadGuard",
                    cta="Travel protected.",
                    work_dir=Path("work"),
                )
                self.assertTrue(output.is_file())
                self.assertGreater(output.stat().st_size, 1000)
            finally:
                os.chdir(Path(__file__).resolve().parent.parent)


def _font_path() -> Path:
    candidate = Path("C:/Windows/Fonts/arialbd.ttf")
    if candidate.is_file():
        return candidate
    from .support import _font

    return _font()


class VoiceTests(unittest.TestCase):
    """The two traps in T2A: a 200 carrying base_resp.error, and hex audio."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _provider(self, transport):
        from nomadguard.providers.minimax import MinimaxHttp, MinimaxVoiceProvider

        return MinimaxVoiceProvider(MinimaxHttp("k", transport=transport), "speech-2.8-turbo", "English_Persuasive_Man")

    def test_url_audio_is_downloaded_and_costed_from_billable_characters(self) -> None:
        from nomadguard.contracts import VoiceRequest

        transport = FakeTransport(
            (200, {
                "data": {"audio": "https://cdn.example/vo.mp3"},
                "extra_info": {"usage_characters": 90, "audio_length": 6500},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            }),
            (200, b"mp3-bytes"),
        )
        destination = self.tmp / "vo.mp3"
        result = self._provider(transport).synthesize(
            VoiceRequest(text="One line.<#0.6#>Two.", voice_id="English_Persuasive_Man"), destination
        )
        self.assertEqual(destination.read_bytes(), b"mp3-bytes")
        self.assertEqual(result.characters, 90)
        self.assertAlmostEqual(result.duration_s, 6.5)
        self.assertAlmostEqual(result.cost_usd, 90 / 1_000_000 * 60.0, places=8)

    def test_hex_audio_is_decoded(self) -> None:
        from nomadguard.contracts import VoiceRequest

        transport = FakeTransport((200, {"data": {"audio": "6d7033"}, "base_resp": {"status_code": 0}}))
        destination = self.tmp / "vo.mp3"
        self._provider(transport).synthesize(
            VoiceRequest(text="hi", voice_id="English_Persuasive_Man"), destination
        )
        self.assertEqual(destination.read_bytes(), b"mp3")

    def test_business_error_inside_a_200_is_raised(self) -> None:
        from nomadguard.contracts import VoiceRequest

        transport = FakeTransport(
            (200, {"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}})
        )
        with self.assertRaises(InsufficientBalance) as ctx:
            self._provider(transport).synthesize(
                VoiceRequest(text="hi", voice_id="English_Persuasive_Man"), self.tmp / "vo.mp3"
            )
        self.assertIn("insufficient balance", str(ctx.exception))

    def test_unknown_speech_model_fails_at_construction(self) -> None:
        from nomadguard.providers.minimax import MinimaxHttp, MinimaxVoiceProvider

        with self.assertRaises(ConfigError):
            MinimaxVoiceProvider(MinimaxHttp("k", transport=FakeTransport()), "speech-9", "voice")

    def test_script_over_the_word_budget_is_refused(self) -> None:
        from nomadguard.pipeline.voiceover import VoiceoverError, write_voiceover

        class Long:
            name = "long"
            model = "long"
            voice_id = "v"

            def generate(self, request):
                return TextResult(
                    text=json.dumps({"first_line": " ".join(["word"] * 20),
                                     "second_line": " ".join(["word"] * 20)}),
                    model="long", cost_usd=0.0,
                )

            def synthesize(self, request, destination):
                raise AssertionError("must not synthesize an over-budget script")

        with self.assertRaises(VoiceoverError):
            write_voiceover(
                brief(), _strategy(), Long(), Long(), start_s=0.6, destination=self.tmp / "vo.mp3"
            )


def _strategy():
    from nomadguard.contracts import Angle, AngleScore, Strategy

    angles = [Angle(name=n, target_emotion="calm", hook="h", concept="c", cta="cta") for n in ("A", "B", "C")]
    scores = [
        AngleScore(angle_name=n, insight_strength=8, hook_viability=8, message_fidelity=8, reason="r")
        for n in ("A", "B", "C")
    ]
    return Strategy(angles=angles, scores=scores, selected_name="A", selection_reason="r", audience_insight="i")


class AudioMixTests(unittest.TestCase):
    def test_assembly_with_a_voiceover_produces_an_audio_stream(self) -> None:
        import subprocess
        from nomadguard.pipeline.assemble import assemble

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=0x101820:s=480x854:r=24:d=12",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "shot.mp4")],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "sine=frequency=220:duration=5", "-c:a", "libmp3lame", str(root / "vo.mp3")],
                check=True, capture_output=True,
            )
            storyboard = make_storyboard([12])
            output = assemble(
                storyboard,
                [root / "shot.mp4"],
                root / "final.mp4",
                ffmpeg="ffmpeg",
                ffprobe="ffprobe",
                font_path=_font_path(),
                brand="NomadGuard",
                cta="Travel protected.",
                work_dir=root / "work",
                voiceover=root / "vo.mp3",
                voice_start_s=0.6,
            )
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(output)],
                capture_output=True, text=True, check=True,
            )
            streams = json.loads(probe.stdout)["streams"]
            self.assertTrue(any(item["codec_type"] == "audio" for item in streams))
            video = next(item for item in streams if item["codec_type"] == "video")
            self.assertEqual(video["codec_name"], "h264")


class SettingsSmokeTests(unittest.TestCase):
    def test_settings_used_by_pipeline_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            self.assertEqual(settings.video_resolution, "480P")
            self.assertGreater(settings.policy.retry_max_attempts, 0)


if __name__ == "__main__":
    unittest.main()
