import type { Job, Providers } from "../api";
import { imageCost, videoCost } from "../wizard";
import { ResultPanel } from "./ResultPanel";
import { Button } from "./Button";
import { Stepper } from "./StepNav";
import { BriefView, StoryboardView, StrategyView } from "./views";

type ProduceProps = {
  job: Job;
  providers: Providers | null;
  busy: boolean;
  onBack: () => void;
  onProduce: () => void;
};

export function ProduceStep({ job, providers, busy, onBack, onProduce }: ProduceProps) {
  const duration = job.storyboard?.total_duration_s ?? 0;
  const video = videoCost(job, providers);
  const images = imageCost(job);
  return (
    <section className="panel" aria-labelledby="step-title">
      <p className="muted small">
        {duration}s across {job.storyboard?.scenes.length ?? 0} shots, plus a voiceover and both QA stages.
      </p>
      <dl className="facts wide">
        <div>
          <dt>Video</dt>
          <dd>
            ${video.toFixed(2)} <span className="muted small">({providers?.video_resolution ?? "?"})</span>
          </dd>
        </div>
        <div>
          <dt>Key frames</dt>
          <dd>${images.toFixed(3)}</dd>
        </div>
        <div>
          <dt>Voiceover + QA</dt>
          <dd className="muted small">a few cents</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{providers?.video.model ?? "…"}</dd>
        </div>
      </dl>
      <p className="muted small">
        Generation is sequential and resumable. If it fails partway, the planning and finished shots are kept.
      </p>
      <div className="actions">
        <Button label="Back" onClick={onBack} disabled={busy} />
        <Button label="Produce the ad" variant="primary" loading={busy} onClick={onProduce} />
      </div>
    </section>
  );
}

export function ProgressStep({ job }: { job: Job }) {
  return (
    <section className="panel" aria-labelledby="step-title">
      <Stepper state={job.state} />
      <p className="muted small" role="status" aria-live="polite">
        {job.substep}
        {job.placeholder ? " · placeholder providers" : ""}
      </p>
      {job.error ? (
        <p className="callout error">
          <strong>{String(job.error.class ?? "Error")}</strong>: {String(job.error.message ?? "")}
        </p>
      ) : null}
      {job.shots.length > 0 ? (
        <ul className="shots">
          {job.shots.map((shot) => (
            <li key={shot.index}>
              <span className={`badge ${shot.state}`}>{shot.state}</span>
              <span>shot {shot.index}</span>
              <span className="muted small">
                {shot.task ? `task ${shot.task.task_id}` : "no task yet"}
                {shot.attempts.length > 0 ? ` · ${shot.attempts.length} attempts` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="muted small">You can leave this page; the job continues on the server.</p>
    </section>
  );
}

type ReviewProps = { job: Job; onRestart: () => void; onResume: () => void; busy: boolean };

export function ReviewStep({ job, onRestart, onResume, busy }: ReviewProps) {
  const failed = job.state === "FAILED";
  return (
    <section className="panel" aria-labelledby="step-title">
      <p className={failed ? "verdict bad" : "verdict ok"} role="status">
        {failed
          ? `Not shipped: ${job.qa?.reasons.join("; ") ?? String(job.error?.message ?? "job failed")}`
          : "Shipped: technical and creative QA both passed."}
      </p>

      <ResultPanel job={job} />

      <h3>The brief you gave it</h3>
      <BriefView brief={job.brief} />

      <h3>Strategy</h3>
      <StrategyView job={job} />

      <h3>Storyboard</h3>
      <StoryboardView job={job} />

      <div className="actions">
        {failed ? <Button label="Resume from checkpoint" loading={busy} onClick={onResume} /> : null}
        <Button label="Start a new ad" onClick={onRestart} disabled={busy} />
      </div>
    </section>
  );
}
