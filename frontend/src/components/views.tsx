import type { Brief, Job } from "../api";

export function BriefView({ brief }: { brief: Brief }) {
  return (
    <dl className="facts wide">
      <div>
        <dt>Brand</dt>
        <dd>{brief.brand}</dd>
      </div>
      <div>
        <dt>Category</dt>
        <dd>{brief.category}</dd>
      </div>
      <div>
        <dt>Audience</dt>
        <dd>{brief.audience}</dd>
      </div>
      <div>
        <dt>Problem</dt>
        <dd>{brief.problem.join(", ")}</dd>
      </div>
      <div>
        <dt>Core message</dt>
        <dd>{brief.core_message}</dd>
      </div>
      <div>
        <dt>Brand feel</dt>
        <dd>{brief.brand_feel.join(", ")}</dd>
      </div>
      <div>
        <dt>Duration / ratio</dt>
        <dd>
          {brief.duration_s}s / {brief.aspect_ratio}
        </dd>
      </div>
      <div>
        <dt>CTA</dt>
        <dd>{brief.cta}</dd>
      </div>
      {brief.visual_notes ? (
        <div>
          <dt>Visual notes</dt>
          <dd>{brief.visual_notes}</dd>
        </div>
      ) : null}
    </dl>
  );
}

export function StrategyView({ job }: { job: Job }) {
  const strategy = job.strategy;
  if (!strategy) return <p className="muted">Strategy pending.</p>;
  const total = (name: string) => {
    const score = strategy.scores.find((item) => item.angle_name === name);
    return score ? score.insight_strength + score.hook_viability + score.message_fidelity : 0;
  };
  return (
    <>
      <p className="muted small">{strategy.audience_insight}</p>
      <div className="table-scroll">
        <table className="angles">
          <thead>
            <tr>
              <th scope="col">Angle</th>
              <th scope="col">Hook</th>
              <th scope="col">Insight</th>
              <th scope="col">Hook</th>
              <th scope="col">Message</th>
              <th scope="col">Total</th>
            </tr>
          </thead>
          <tbody>
            {strategy.angles.map((angle) => {
              const score = strategy.scores.find((item) => item.angle_name === angle.name);
              return (
                <tr
                  key={angle.name}
                  className={angle.name === strategy.selected_name ? "selected-row" : undefined}
                >
                  <th scope="row">{angle.name}</th>
                  <td>{angle.hook}</td>
                  <td>{score?.insight_strength ?? "-"}</td>
                  <td>{score?.hook_viability ?? "-"}</td>
                  <td>{score?.message_fidelity ?? "-"}</td>
                  <td>
                    <strong>{total(angle.name)}</strong>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="callout info">
        <strong>{strategy.selected_name}</strong> selected. {strategy.selection_reason}
      </p>
    </>
  );
}

export function StoryboardView({ job }: { job: Job }) {
  const storyboard = job.storyboard;
  if (!storyboard) return <p className="muted">Storyboard pending.</p>;
  return (
    <>
      <p className="muted small">
        {storyboard.total_duration_s}s / {storyboard.aspect_ratio} · {storyboard.continuity_block}
      </p>
      <ol className="scenes">
        {storyboard.scenes.map((scene) => (
          <li key={scene.index}>
            <div className="scene-head">
              <span className={`badge ${scene.beat}`}>{scene.beat}</span>
              <strong>
                {scene.start_s.toFixed(0)}–{(scene.start_s + scene.duration_s).toFixed(0)}s
              </strong>
              <span className="muted small">{scene.camera}</span>
            </div>
            <p>{scene.description}</p>
            {scene.on_screen_text ? <p className="small">On screen: “{scene.on_screen_text}”</p> : null}
            <details>
              <summary>Model prompt</summary>
              <p className="small">{scene.provider_prompt}</p>
            </details>
          </li>
        ))}
      </ol>
      {job.voiceover ? (
        <>
          <h3>Voiceover</h3>
          <p className="callout info">
            “{job.voiceover.text.replace(/<#[0-9.]+#>/g, " … ")}”
            <span className="muted small"> · {job.voiceover.voice_id}, {job.voiceover.duration_s.toFixed(1)}s</span>
          </p>
        </>
      ) : null}
    </>
  );
}
