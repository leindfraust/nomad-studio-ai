# NomadGuard Ad Studio

Turns a marketing brief into a finished 9:16 social advertisement: three creative
angles, a reasoned selection, a shot-by-shot storyboard, real video generation,
then technical and creative QA before anything is called final.

The engineering point is the seam: text, vision, image and video are four separate
capability interfaces, each bound to a provider and model by configuration. The
pipeline asks a provider what it can do and plans inside that answer, so swapping
the video vendor changes the cut, not the code.

## Status

- Pipeline, API, UI and tests are complete; 102 backend tests and 7 UI tests pass.
- A real advertisement ships at `submission/nomadguard-ad-final.mp4`: 1080x1920,
  13.4 s, H.264 + AAC voiceover, exactly 9:16, 5.6 MB, produced at 768P for $0.50
  and conformed to the platform ratio without regenerating. `submission/README.md`
  records the provenance and the conform command.
- **It passes the gate, narrowly.** Technical QA passed; creative QA scored mean 7.0
  (realism 7, consistency 8, brief 7, hook 6, ad quality 7), with the storyboard
  fidelity at 6 reported but not gated. `submission/qa.json` carries the report.
- The pass is honest but borderline. The critic's own defect list still stands: the
  opening is a familiar rear-view airport walk, two shots are close to identical, and
  the luggage arrangement is implausible. What carries the ad is the hook line, the
  product plate and a clean end card, not the footage.
- Five paid passes were attempted, $2.15 total. Earlier cuts failed at 5.4 and 6.4;
  the gate was never lowered to make one pass.
- Three generation passes were attempted ($1.14 total). The gate is not lowered to
  make a run pass; what to do about the shortfall is recorded in
  `AI_USAGE.md`.

## What the ad is

A 13 second brand film: a traveller in a dusk terminal, a hook line, a held beat,
then a brand end card with the CTA, over a calm voiceover. It reads as a premium
travel brand spot. It does not demonstrate the product on screen.

## Known creative gap

The brief asks the ad to communicate protection. With text-to-video of this class,
the honest options were: show an app screen (generated screens came back black or
as garbled pseudo-text) or show no product at all. The second was chosen and the
QA stage recorded the cost of that choice. Fixing it properly means either shooting
real UI as an overlay in post, or a higher video tier with reference images of a
designed screen.

## Quick start

Requires Python 3.12+, Node 20+, ffmpeg/ffprobe, and one MiniMax API key.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate elsewhere
pip install -e .
npm --prefix frontend ci
cp .env.example .env              # then set MINIMAX_API_KEY
npm run build                     # builds frontend/dist for production
npm run dev                       # API on :8787 and Vite on :5173, both in one terminal
```

`npm run dev` runs `scripts/dev.py`, which starts uvicorn and Vite together and
stops both on Ctrl+C. It finds `.venv` itself and launches Vite through node
directly, so it does not matter which `python` your shell resolves or whether the
venv is active. Open http://localhost:5173.

## Tutorial

A two minute walkthrough, recorded from the running app:
`docs/tutorial/nomadguard-studio-tutorial.mp4` (1280x720, 2:07). It covers all six
steps, and it shows a run that **fails** the quality gate, followed by the cut that
passed — because that is what the product does. The script and the production
method are in `docs/tutorial/README.md`.

## The interface

Six steps, each gated by what actually exists:
```
1 Brief → 2 Strategy → 3 Storyboard → 4 Produce → 5 Progress → 6 Review
```

Planning and production are separate actions, so you read the three angles, the
scored selection and the shots before any video is billed. Step 4 shows the cost of
the cut. Step 6 is the full record: the video, both QA reports, and every input that
produced them. A step you cannot reach yet is disabled rather than hidden.

### Configuration

`.env` is authoritative: it overrides variables inherited from your shell, so a stale
`NOMADGUARD_*` export cannot silently turn a live setup into an offline demo. Check
what actually resolved with `python -m nomadguard providers` — the same bindings are
printed in the app header.

The key lives in `.env` as `MINIMAX_API_KEY=...`. That file is git-ignored.

Offline providers produce labelled placeholder footage and are refused unless
`NOMADGUARD_ALLOW_PLACEHOLDER=1`, so a missing key fails loudly instead of shipping a
fake ad.

Production instead serves the built UI from FastAPI:

```bash
npm run build
python -m nomadguard serve --no-reload
```

CLI, same runner as the API:

```bash
python -m nomadguard providers                      # models, capabilities, prices
python -m nomadguard run --brief briefs/nomadguard.json --dry-run
python -m nomadguard run --brief briefs/nomadguard.json
python -m nomadguard status <job-id>
python -m nomadguard show <job-id> --stage strategy
python -m nomadguard resume <job-id>
```

## Architecture

```
React TSX + Vite  ──HTTP──>  FastAPI (routes, lifespan, executor)
                                  │
                                  └──> JobRunner (one state machine)
                                         ├── pipeline/strategy.py     3 angles + scored selection
                                         ├── pipeline/storyboard.py   legal shot partition + scenes
                                         ├── pipeline/prompts.py      vendor-neutral intent
                                         ├── providers/base.py        Text/Vision/Image/Video protocols
                                         ├── jobs/store.py            jobs/<id>/job.json, atomic writes
                                         ├── pipeline/assemble.py     ffmpeg concat + burned copy
                                         └── pipeline/qa_*.py         ffprobe facts + vision rubric
```

The CLI and the HTTP API call the same `JobRunner`; no workflow logic lives in a
handler. Job state is the filesystem: `jobs/<id>/job.json` plus artifacts, written
atomically after every transition, so a killed process is resumable and a
reviewer can read the state with `cat`.

## Providers and models

| Capability | Default | Price |
|---|---|---|
| text | `MiniMax-M3.1-Flash` | $0.15 / $0.60 per M tokens |
| vision | `MiniMax-M3.1-Flash` (multimodal) | same |
| image | `image-01` | $0.0035 per image |
| video | `MiniMax-H3-Lite` at 480P | $0.025 per second |

Each is chosen independently in `.env`:

```
NOMADGUARD_VIDEO_PROVIDER=minimax
NOMADGUARD_VIDEO_MODEL=MiniMax-H3-Lite
NOMADGUARD_VIDEO_RESOLUTION=480P
```

Implemented: `minimax` (all four capabilities), `openai_compat` (text and vision
against any verified-compatible chat endpoint), `offline` (deterministic
placeholders for tests and demos). Unknown providers and models raise at startup;
nothing falls back silently.

### Adding a vendor that needs a different API

1. Implement the protocols in `nomadguard/providers/base.py` for the capabilities
   you need, in `nomadguard/providers/<vendor>.py`.
2. Return documented `VideoCapabilities` (durations, ratios, resolutions, reference
   inputs, per-second prices, source URL, `verified=False` until a live call proves
   it).
3. Add the name to the closed sets in `config.PROVIDERS` and to `factory._Shared`.
4. Run `python -m nomadguard providers` and `python -m unittest discover -s tests`.

Selecting an implemented provider is config only. Adding a genuinely different
wire protocol is one adapter file plus a registration line: the creative stages
never learn the vendor's name.

## How a run works

1. **Strategy.** Two JSON calls: three genuinely different angles, then scores for
   insight strength, hook viability and message fidelity. The winner is computed
   from the scores (`max(total, hook_viability)`), not taken from the model's
   claim, so the rationale and the choice cannot disagree.
2. **Storyboard.** The planner asks the video provider for legal durations and
   partitions the target: 12 s at 4-15 s becomes 4+4+4; 10 s becomes 5+5 because
   three 4 s shots exceed it; a provider limited to 4/6/8 s gives 4+6. If no legal
   partition exists the run fails with `CapabilityMismatch` rather than emitting a
   storyboard the model cannot render.
3. **Prompts.** Each scene keeps two artifacts: a vendor-neutral intent, and a
   prompt rendered in the provider's dialect (`[slow push in]` for MiniMax). The
   copy is never inside a video prompt; generated text comes back warped.
4. **Key frames.** One image per shot, the first plain, later ones with
   `subject_reference` set to the hero frame so the same person and wardrobe recur.
5. **Generation.** Sequential by default. Each task id is persisted before polling,
   so `resume` polls instead of paying twice.
6. **Assembly.** ffmpeg normalizes every shot to the first shot's canvas, concatenates
   with hard cuts on action, burns the hook line at 0.4-3.2 s (above the Reels
   caption zone) and a brand plus CTA end card over the last 2 s, then encodes
   H.264 / yuv420p / `+faststart`.
7. **QA.** Technical checks are deterministic; creative scores are a rubric the
   vision model fills, with the gate computed here.

## Job lifecycle

```
CREATED -> PLANNING -> GENERATING -> EVALUATING -> FINALIZED
               \           \            \
                `-----------+------------`-> FAILED
```

`substep` records where inside a state the run is (`strategy`, `shot-1`,
`assembling`), so the five states stay readable while the log still shows detail.

| Failure | Retried | Why |
|---|---|---|
| 429 rate limited | yes, honouring `Retry-After` | transient |
| 5xx / timeout on a read | yes, backoff | transient |
| shot reported `failed` | no, needs explicit resume | a new generation costs money |
| submission outcome unknown | no | the remote task may exist and bill |
| 402 insufficient balance | no | retrying spends nothing |
| 422 safety refusal | no | the same text will be refused again |
| capability mismatch | no | a configuration bug |

`resume` reads the checkpoint. A shot with an in-flight task id is polled, not
resubmitted; completed shots are skipped; assembly and QA continue from whatever
artifact exists. `status` shows a derived `stalled` flag when a live job has not
been updated for the configured window, so a crashed run is visible instead of
silently pending.

## QA

Technical: file exists and is non-empty, ffprobe parses it, a full decode succeeds
(not just a header check), duration inside 10-15 s within tolerance, aspect within
tolerance of 9:16, short edge above the configured floor, H.264 / yuv420p.

Creative: six frames (start and midpoint of each shot) are sent to the vision model
with the brief and storyboard, scored 0-10 on realism, visual consistency, brief
adherence, hook strength and advertising quality, plus a concrete defect list. The
job only reaches `FINALIZED` when technical QA passes and the mean and per-dimension
scores clear the configured thresholds. Otherwise it is `FAILED` with both reports
and the video still on disk.

## Cost

One 12 s ad at 480P, list prices:

| Item | Cost |
|---|---|
| strategy + scores + storyboard prompts | ~$0.02 |
| 3 key frames at $0.0035 | ~$0.0105 |
| video, 12 s x $0.025 (480P) | $0.30 |
| video, 12 s x $0.04 (768P) | $0.48 |
| voiceover, speech-2.8-turbo | ~$0.005 |
| vision QA | ~$0.01 |
| **total, 480P** | **~$0.34** |
| **total, 768P** | **~$0.52** |

A 4 s shot rerun adds $0.10. `NOMADGUARD_VIDEO_MODEL=MiniMax-H3` with
`768P`/`2K` is a config change at $0.08/$0.13 per second.

## Tests

```bash
python -m unittest discover -s tests -t .     # 77 backend tests
npm --prefix frontend test                    # 4 UI tests
npm --prefix frontend run typecheck
```

The backend suite covers the four places this could quietly be wrong: shot
partitioning against real capability sets, provider wire mapping and error
classification (including ambiguous submissions), durable jobs and resume without
re-billing, and the QA decision arithmetic. One test assembles a real clip through
ffmpeg from a relative working directory, because that is how the bug it guards
appeared.

## Deliberately not implemented

- Music, voiceover, sound design. The ad is silent by design.
- Automatic regeneration inside QA. A failed gate stops and asks; a loop would
  spend money unattended.
- Parallel shot generation. A budget account rate-limited mid-run produces a worse
  demo than one that takes longer.
- A database, queue service, multi-tenant auth, or horizontal workers.
- 1080p or 2K output. 480P is the cheapest tier; the upgrade is one env var.

## With another day

- H3-Context-IR prompt expansion before generation.
- Shot-level creative QA, so a weak shot is regenerated before the cut.
- A second video vendor wired through the same protocols and asserted in CI, so
  the swap is proven by tests rather than described in prose.
- An end card typography check on the rendered frame (measured text bounds, not a
  width heuristic).
- Postgres or SQLite for jobs once more than one person runs this.

## Layout

```
nomadguard/            backend package (contracts, config, providers, pipeline, jobs, web)
frontend/              React TSX + Vite app
tests/                 backend suite and fixtures
briefs/                the NomadGuard brief
scripts/dev.py         runs API + Vite together
docs/superpowers/      design doc and implementation plan
submission/            final video and sanitized evidence
```

`jobs/` and `.env` are ignored: run state and credentials never enter git.
