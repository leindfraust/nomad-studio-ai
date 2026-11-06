# Tutorial: NomadGuard Ad Studio

A 1:39 walkthrough of the six-step workflow, recorded from the running app. No intro
card: it opens on the brief, and each narration chunk starts where its screen starts.

- Video: `nomadguard-studio-tutorial.mp4` (1920x1080, 1:39, 2.7 MB)
- Narration: eleven-v4-turbo via PromptWise, six screen-sized chunks
- Screen capture: OpenScreen, recording the app's Chrome window
- Project: `proj_7b3b9816-c995-4794-81d3-add7b9453829` (OpenScreen)

## What the video shows

| Time | Step | What happens |
|---|---|---|
| 0:00 | 1 Brief | The form every later stage is generated from |
| 0:12 | 2 Strategy | Three angles scored; winner picked by the scores |
| 0:30 | 3 Storyboard | Three shots, continuity block, model-ready prompt |
| 0:42 | 4 Produce | The cost before committing: $0.48 at 768P |
| 0:52 | 5 Generating | Resumable; every transition is on disk |
| 1:06 | 6 Review | QA refuses this run at 5.6; the 7.0 cut follows at 1:26 |

The run recorded for the tutorial did **not** pass the gate, and the video says so.
That is the product behaving correctly, not a failed take: the creative score is
5.6 against a 7.0 bar, `brief_adherence` 4.0. The accepted cut plays after it,
framed as "the cut that passed".

## Running the app the way the video does

```bash
python -m venv .venv && .venv\Scripts\activate     # or: source .venv/bin/activate
pip install -e .
npm --prefix frontend ci
cp .env.example .env                                # set MINIMAX_API_KEY
npm run dev                                         # API :8787 + UI :5173
```

Then, in the browser:

1. **Brief** — the fields are preloaded from `briefs/nomadguard.json`.
2. **Plan the ad** — spends a fraction of a cent; three angles plus a storyboard.
3. **Strategy** — read the three angles and the reason the winner won.
4. **Storyboard** — read the shots; each carries its model prompt.
5. **Produce** — shows the video cost for this exact cut before you commit.
6. **Review** — the video, both QA reports, and every input that produced them.

Back works at every step. Unreachable steps are disabled, not hidden.

## How the video was produced

1. `openscreen.startRecording({ window: "NomadGuard Ad Studio" })` against the
   Chrome window showing the app.
2. The wizard was driven through all six steps; the recording ran 295.7 s and
   includes the full planning and generation waits.
3. `openscreen.addTrims` removed the opening dead time, three inter-step gaps and
   the generation wait, leaving 99 s including the appended cut.
4. Six narration chunks were generated with `promptwise.generate_audio`
   (eleven-v4-turbo): one per screen, so no sentence describes a screen that has
   already been cut. They sit at ruler 0, 64, 88, 106, 122 and 270, each inside a
   kept segment.
5. The accepted ad was imported and appended with `openscreen.insertClip`, and two
   text annotations were added: the title, and the pass score over the final clip.
6. `setAspectRatio 16:9`, dark wallpaper `#0B1220`, then `exportVideo` at 1080p/30.

Narration text lives next to the generated MP3s in `audio/`, so the script can be
regenerated without the recording.
