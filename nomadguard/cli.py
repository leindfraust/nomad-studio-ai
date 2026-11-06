"""CLI. Every command calls the same runner the API uses."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_env_file, load_settings
from .contracts import Brief, JobRecord, JobState
from .errors import ConfigError, NomadGuardError
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .providers.factory import build_providers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nomadguard", description="Brief to social ad.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="plan, generate, evaluate one brief")
    run.add_argument("--brief", required=True, type=Path)
    run.add_argument("--dry-run", action="store_true", help="stop after the storyboard")
    run.add_argument("--json", action="store_true", help="print the job record as JSON")

    status = sub.add_parser("status", help="current state of a job")
    status.add_argument("job_id")

    show = sub.add_parser("show", help="inspect one stage of a job")
    show.add_argument("job_id")
    show.add_argument("--stage", required=True, choices=["brief", "strategy", "storyboard", "qa"])

    resume = sub.add_parser("resume", help="continue a job from its last checkpoint")
    resume.add_argument("job_id")
    resume.add_argument("--json", action="store_true", help="print the job record as JSON")

    providers = sub.add_parser("providers", help="configured capabilities and prices")
    providers.add_argument("--json", action="store_true")

    serve = sub.add_parser("serve", help="run the API and the built UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--no-reload", action="store_true")

    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except NomadGuardError as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    if args.command == "serve":
        import uvicorn

        load_env_file(Path(".env"))
        load_settings()  # fail here with a readable message, before uvicorn starts
        uvicorn.run(
            "nomadguard.web.server:create_app_from_env",
            factory=True,
            host=args.host,
            port=args.port,
            reload=not args.no_reload,
        )
        return 0

    load_env_file(Path(".env"))
    settings = load_settings()
    store = JobStore(settings.paths.jobs_dir)

    if args.command == "providers":
        _print_providers(settings, as_json=args.json)
        return 0

    runner = JobRunner(store, build_providers(settings), settings)

    if args.command == "run":
        brief = Brief.model_validate_json(args.brief.read_text(encoding="utf-8"))
        record = store.create(brief, dry_run=args.dry_run)
        print(f"job {record.id} created ({'dry run' if args.dry_run else 'full run'})")
        record = runner.run(record.id)
        return _report(record, as_json=args.json)

    if args.command == "resume":
        record = runner.run(args.job_id, retry_failed=True)
        return _report(record, as_json=args.json)

    if args.command == "status":
        _print_status(store.get(args.job_id), settings.policy.staleness_s)
        return 0

    if args.command == "show":
        record = store.get(args.job_id)
        payload = getattr(record, args.stage)
        if payload is None:
            print(f"{args.stage} has not been produced yet")
            return 1
        print(json.dumps(payload.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0

    raise AssertionError(f"unhandled command {args.command}")


def _report(record: JobRecord, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(record.model_dump(mode="json"), indent=2, ensure_ascii=False))
    else:
        print(f"state: {record.state} ({record.substep})")
        print(f"estimated cost: ${record.costs.estimated_usd:.4f}")
        if record.costs.reported_usd is not None:
            print(f"provider-reported cost: ${record.costs.reported_usd:.4f}")
        if record.state == JobState.FINALIZED:
            print(f"final video: {record.id}/artifacts/final.mp4")
            if record.placeholder:
                print("NOTE: placeholder providers produced this file. It is not the submitted ad.")
        if record.error:
            print(f"error: {record.error.get('class')}: {record.error.get('message')}")
        for reason in (record.qa.reasons if record.qa else []):
            print(f"  qa: {reason}")
    return 0 if record.state in (JobState.FINALIZED, JobState.PLANNING) else 1


def _print_status(record: JobRecord, staleness_s: float) -> None:
    from .contracts import utcnow

    age = (utcnow() - record.updated_at).total_seconds()
    stalled = age > staleness_s and record.state not in (JobState.FINALIZED, JobState.FAILED)
    print(f"job {record.id}")
    print(f"state: {record.state} ({record.substep})")
    print(f"updated: {age:.0f}s ago{' - STALLED' if stalled else ''}")
    print(f"shots: {len(record.shots)}")
    for shot in record.shots:
        task = shot.task.task_id if shot.task else "-"
        print(f"  shot {shot.index}: {shot.state} task={task} attempts={len(shot.attempts)}")
    if record.error:
        print(f"error: {record.error.get('class')}: {record.error.get('message')}")
    if record.qa:
        status = "passed" if record.qa.passed else "failed"
        print(f"qa: {status}")


def _print_providers(settings, *, as_json: bool) -> None:
    from .providers.factory import build_providers

    built = build_providers(settings)
    video = built.video.capabilities()
    payload = {
        "capabilities": settings.public()["capabilities"],
        "video": {
            "model": video.model,
            "durations_s": [video.durations_s[0], video.durations_s[-1]],
            "ratios": video.ratios,
            "resolutions": video.resolutions,
            "first_frame": video.supports_first_frame,
            "reference_image": video.supports_reference_image,
            "price_per_second_usd": video.price_per_second_usd,
            "source": video.source_url,
            "verified": video.verified,
        },
        "resolution": settings.video_resolution,
    }
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(f"text   : {settings.text.provider} / {settings.text.model}")
    print(f"vision : {settings.vision.provider} / {settings.vision.model}")
    print(f"image  : {settings.image.provider} / {settings.image.model}")
    print(f"video  : {video.model} at {settings.video_resolution}")
    print(f"         durations {video.durations_s[0]}-{video.durations_s[-1]}s, ratios {', '.join(video.ratios)}")
    print(f"         resolutions {', '.join(video.resolutions)}, first frame: {video.supports_first_frame}")
    prices = ", ".join(f"{res} ${price}/s" for res, price in video.price_per_second_usd.items())
    print(f"         {prices}")
    print(f"         source: {video.source_url}")
    print(f"         verified by a live call: {video.verified}")


if __name__ == "__main__":
    raise SystemExit(main())
