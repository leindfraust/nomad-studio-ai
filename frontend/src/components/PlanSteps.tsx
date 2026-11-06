import type { Brief } from "../api";
import { Button } from "./Button";
import { BriefView, StoryboardView, StrategyView } from "./views";
import type { Job } from "../api";

type FieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiline?: boolean;
  type?: "text" | "number";
  min?: number;
  max?: number;
};

function Field({ label, value, onChange, multiline, type = "text", min, max }: FieldProps) {
  const id = `brief-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
  return (
    <label className="field" htmlFor={id}>
      <span>{label}</span>
      {multiline ? (
        <textarea id={id} name={id} rows={3} value={value} onChange={(event) => onChange(event.target.value)} />
      ) : (
        <input
          id={id}
          name={id}
          type={type}
          min={min}
          max={max}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  );
}

type BriefProps = {
  brief: Brief;
  busy: boolean;
  pending: boolean;
  onChange: (brief: Brief) => void;
  onPlan: () => void;
};

export function BriefStep({ brief, busy, pending, onChange, onPlan }: BriefProps) {
  const update = <K extends keyof Brief>(key: K, value: Brief[K]) => onChange({ ...brief, [key]: value });
  const working = busy || pending;
  return (
    <section className="panel" aria-labelledby="step-title">
      <p className="muted small">Everything downstream is generated from these fields.</p>
      <div className="grid-two">
        <Field label="Brand" value={brief.brand} onChange={(v) => update("brand", v)} />
        <Field label="Category" value={brief.category} onChange={(v) => update("category", v)} />
      </div>
      <Field label="Audience" value={brief.audience} onChange={(v) => update("audience", v)} multiline />
      <Field
        label="Problem (comma separated)"
        value={brief.problem.join(", ")}
        onChange={(v) => update("problem", v.split(",").map((i) => i.trim()).filter(Boolean))}
      />
      <Field label="Core message" value={brief.core_message} onChange={(v) => update("core_message", v)} multiline />
      <Field
        label="Brand feel (comma separated)"
        value={brief.brand_feel.join(", ")}
        onChange={(v) => update("brand_feel", v.split(",").map((i) => i.trim()).filter(Boolean))}
      />
      <div className="grid-two">
        <Field
          label="Duration (seconds, 10–15)"
          type="number"
          min={10}
          max={15}
          value={String(brief.duration_s)}
          onChange={(v) => update("duration_s", Number(v))}
        />
        <Field label="Call to action" value={brief.cta} onChange={(v) => update("cta", v)} />
      </div>
      <Field
        label="Visual notes"
        value={brief.visual_notes ?? ""}
        onChange={(v) => update("visual_notes", v || null)}
        multiline
      />
      <div className="actions">
        <Button label="Plan the ad" variant="primary" loading={working} onClick={onPlan} />
      </div>
      {working ? (
        <p className="muted small" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          Writing three angles, scoring them, then the storyboard. The result appears by itself.
        </p>
      ) : (
        <p className="muted small">Planning costs a few cents: three angles, a scored selection and a storyboard.</p>
      )}
    </section>
  );
}

type ReviewProps = {
  job: Job;
  busy: boolean;
  onBack: () => void;
  onContinue: () => void;
};

export function StrategyStep({ job, busy, onBack, onContinue }: ReviewProps) {
  return (
    <section className="panel" aria-labelledby="step-title">
      {job.strategy ? (
        <StrategyView job={job} />
      ) : (
        <p className="muted" role="status">
          Analysing the brief…
        </p>
      )}
      <div className="actions">
        <Button label="Back" onClick={onBack} disabled={busy} />
        <Button label="Continue" variant="primary" onClick={onContinue} disabled={!job.strategy} />
      </div>
    </section>
  );
}

export function StoryboardStep({ job, busy, onBack, onContinue }: ReviewProps) {
  return (
    <section className="panel" aria-labelledby="step-title">
      {job.storyboard ? (
        <StoryboardView job={job} />
      ) : (
        <p className="muted" role="status">
          Writing the shots…
        </p>
      )}
      <div className="actions">
        <Button label="Back" onClick={onBack} disabled={busy} />
        <Button label="Continue" variant="primary" onClick={onContinue} disabled={!job.storyboard} />
      </div>
    </section>
  );
}

export function BriefSummary({ brief }: { brief: Brief }) {
  return <BriefView brief={brief} />;
}
