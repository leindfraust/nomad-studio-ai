# AI usage

## Tools

- **OpenCode** (this session, model `deepseek-v4.1-flash`) with the `superpowers`
  skills: brainstorming, writing-plans, executing-plans, test-driven development,
  verification. Used to design, plan and implement the whole repository.
- **Context7 MCP** for current FastAPI and Vite documentation (lifespan, static
  serving, dev proxy, build/type-check split).
- **MiniMax public API docs** for the provider contract, pricing and limits.
- **MiniMax-M3.1-Flash** at runtime: the application itself calls it for the
  angles, the scoring, the storyboard and the visual QA. That is a product
  dependency, not a development tool.

No code was pasted in from another project. The provider architecture follows one
convention throughout: a capability interface per concern, a factory over a closed
set of names, strict boundary contracts, and an explicit failure instead of a
silent fallback.

## What was AI-generated

Effectively all of it: contracts, config parsing, the four provider adapters, the
job store and runner, the creative pipeline, the ffmpeg assembly, the FastAPI
surface, the React app, the tests and this documentation. The human set the
requirements (budget models, MiniMax, React + TypeScript + Vite, FastAPI + Uvicorn,
a monorepo, one dev command for both servers) and reviewed each step.

## One AI decision I rejected

The strategy stage first returned `selected_name` from the model together with its
scores. I removed that: the winner is now computed from the scores in
`pipeline/strategy.py` (`max(total, hook_viability, listed order)`), so a model that
scores one angle highest and names another cannot produce a job whose rationale
contradicts its choice. The model still writes the justification, but it cannot
pick the winner.

The first live run showed the model arguing for one angle while the scores selected
another. The fix is a second, cheap call that rewrites the rationale for the
computed winner, and a validator (`_justifies_another_angle`) that triggers it only
when the prose names a different angle and never names the winner.

A second, smaller one: the first `openai_compat` design read the model id from
`OPENAI_COMPAT_TEXT_MODEL` while the capability binding also carried a model. Two
sources for one value drift. The binding is now the only source.

## One AI problem I detected and corrected

**The shot partition was invalid.** The design document claimed a 10 s target could
be split into `3+3+4` seconds on MiniMax H3. H3 has a 4 s minimum, so those shots
cannot exist. It survived the first draft because it read plausibly. A fresh
reviewer pass caught it, and the rule is now a pure function with tests: 12 s
gives 4+4+4, 10 s gives 5+5 on H3, and a provider limited to 4/6/8 s gives 4+6 for
the same 10 s target.

**Relative paths versus ffmpeg's working directory.** Unit tests passed with an
absolute temp job directory; the first real HTTP run failed with "Error opening
output ... No such file or directory". ffmpeg was being run with `cwd=work_dir` for
the font file while receiving relative input paths. Fixed by resolving every path
before the call, with a regression test that assembles a real clip from a relative
working directory.

**Clipped end card.** The first rendered end card lost both edges of the CTA: the
font size was fixed and `drawtext` does not wrap. Caught by extracting a frame and
looking at it, not by a test. Fixed with greedy word wrapping plus a width-aware
font size, both pure functions with their own tests.

## Supervision notes

- Every claim about a live API in this repository comes from vendor documentation,
  and the capability descriptor marks itself `verified: false` until a real call
  confirms it. `python -m nomadguard providers` prints that flag.
- The offline providers exist so the whole workflow can be demonstrated without
  spending money. They are labelled `placeholder` in the job record and in the UI,
  because a reviewer must never mistake placeholder footage for the submission.
- Provider failures are classified rather than caught broadly. A 402, a 422 and an
  ambiguous create are not retried; a 429 and a 5xx read are.

## Run log: what the live passes taught me

Four paid passes, $1.14 total against a $5 credit. Reported because the challenge
asks how AI output was supervised, and because three of these were my errors, not
the model's.

| Run | Outcome | Cause | Fix |
|---|---|---|---|
| 480P | stopped at video create, $0.0126 | account had no media balance (402/2067) | user enabled credits |
| 480P | creative 5.4, `visual_consistency` 5 | shot 1 had a wheeled case, shot 2 a leather holdall; departure boards carried garbled pseudo-text | later shots now take the hero frame as a `reference_image` instead of a per-shot first frame; prompts forbid readable screens |
| 768P | creative 5.0, `brief_adherence` 4 | after forbidding screens, the ad showed no product moment at all | storyboard now requires one non-textual product beat, and QA samples the end card |
| 768P | creative 5.0, `brief_adherence` 3 | the generated phone screen came back black; a wheel-snag beat never staged | added a deterministic product plate in post, and split `brief_adherence` (judged against the brief) from `storyboard_fidelity` (reported, non-gating), because the rubric was scoring the client's brief against our production plan |
| 768P re-score | creative 6.4, `brief_adherence` 5 | footage still shows no product interaction, only a plate | not fixed: recorded rather than papered over |
| 768P (QA pass) | **creative 7.0, FINALIZED**, $0.50 | hook 6.0 and two near-identical shots | accepted, with the critic's defect list kept in `qa-768p.json` |

### What a DevTools pass found that the unit tests could not

Run against the live UI with Chrome DevTools (console, network, accessibility tree,
Lighthouse):

- `favicon.ico` 404 on every load: no icon link in `index.html`. Fixed.
- Nine form fields with no `id`/`name` (Chrome's form-field issue). Each field now has an
  id derived from its label and is associated with it. Fixed.
- The duration input reported `valuemin=0 valuemax=0`: the min/max props were never passed
  to it. Now 10 and 15, matching the brief's own limit. Fixed.
- Lighthouse `label-content-name-mismatch` on all six step buttons: the `aria-label`
  ("Step 1: Brief") did not contain the visible text ("1 Brief"). Removed; the accessible
  name now comes from the content. Accessibility 100, Best Practices 100, SEO 100.
- No meta description, no `robots.txt`: added both.
- Console showed duplicate `GET /api/brief` and `/api/providers` on load. React StrictMode
  double-invokes effects in development only; production sends one of each.

One dev-only trap worth recording: after editing `PlanSteps.tsx`, Vite kept serving a
stale transform that had the new `id`s but not the new `min`/`max`, and the browser
silently ran old code. A server restart cleared it. When a browser check contradicts the
source, verify what the server *sends* before debugging the component.

Two code defects the live passes exposed, both now covered by tests:

- `resume` was a no-op on a `FAILED` job, because `run()` treated failure as
  terminal. A failed job with paid artifacts must be continueable; it now clears the
  error and re-enters the machine, keeping every completed artifact.
- Creative QA rejected `6.5` because scores were typed as integers. A one-decimal
  score is a normal answer on a 0-10 rubric; the validator now accepts any number
  in range and the run no longer dies after paying for the video.

**The honest conclusion.** The pipeline works: it plans, generates, assembles,
mixes, measures and refuses to call a weak result finished. The advertisement itself
does not clear the bar this project set, and that bar was not lowered to make it
pass. What I would do with another day is in the README: put a designed UI overlay
in post rather than asking a video model to invent one.
