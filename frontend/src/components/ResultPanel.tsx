import type { Job } from "../api";

const DIMENSIONS: Array<[string, string]> = [
  ["realism", "Realism"],
  ["visual_consistency", "Consistency"],
  ["brief_adherence", "Brief"],
  ["hook_strength", "Hook"],
  ["advertising_quality", "Ad quality"],
];

function Score({ label, value }: { label: string; value: number | undefined }) {
  const score = value ?? 0;
  return (
    <div className="score">
      <span>{label}</span>
      <strong>{value ?? "–"}</strong>
      <span className="meter" aria-hidden="true">
        <span style={{ width: `${(score / 10) * 100}%` }} />
      </span>
    </div>
  );
}

export function ResultPanel({ job }: { job: Job }) {
  const qa = job.qa;
  if (!qa) return null;
  const creative = qa.creative;
  return (
    <section className="result" aria-labelledby="result-heading">
      <h2 id="result-heading">Result</h2>
      {job.placeholder ? (
        <p className="callout warning">
          Placeholder footage from offline providers. This is not the submitted advertisement.
        </p>
      ) : null}

      <div className="result-grid">
        <div className="result-media">
          <video
            className="player"
            controls
            playsInline
            preload="metadata"
            src={`/api/jobs/${job.id}/files/final.mp4`}
          />
          <p className="muted small">
            <code>/api/jobs/{job.id}/files/final.mp4</code>
          </p>
        </div>

        <div className="result-body">
          <h3>Technical</h3>
          <ul className="facts">
            <li>
              <span>Duration</span>
              <strong>{qa.technical.duration_s.toFixed(2)}s</strong>
            </li>
            <li>
              <span>Size</span>
              <strong>
                {qa.technical.width}×{qa.technical.height} ({qa.technical.aspect_ratio})
              </strong>
            </li>
            <li>
              <span>Codec</span>
              <strong>{qa.technical.codec ?? "unknown"}</strong>
            </li>
            <li>
              <span>Playable</span>
              <strong>{qa.technical.decodable ? "yes" : "no"}</strong>
            </li>
          </ul>
          {qa.technical.failures.length > 0 ? (
            <ul className="defects">
              {qa.technical.failures.map((failure) => (
                <li key={failure}>{failure}</li>
              ))}
            </ul>
          ) : null}

          {creative ? (
            <>
              <h3>Creative</h3>
              <div className="scores">
                {DIMENSIONS.map(([key, label]) => (
                  <Score key={key} label={label} value={creative.scores[key]} />
                ))}
              </div>
              <p className="muted small">
                Judged by {creative.model}. Storyboard fidelity {creative.scores["storyboard_fidelity"] ?? "–"}/10 is
                reported, not gated.
              </p>
              {creative.defects.length > 0 ? (
                <ul className="defects">
                  {creative.defects.map((defect) => (
                    <li key={defect}>{defect}</li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : null}

          <p className={qa.passed ? "verdict ok" : "verdict bad"}>
            {qa.passed ? "Quality gate passed" : `Quality gate failed: ${qa.reasons.join("; ")}`}
          </p>
          <p className="muted small">
            Estimated cost ${job.costs.estimated_usd.toFixed(4)}
            {job.costs.reported_usd === null
              ? " · provider reported no per-task cost"
              : ` · provider reported $${job.costs.reported_usd.toFixed(4)}`}
          </p>
        </div>
      </div>
    </section>
  );
}
