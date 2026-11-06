import type { Job } from "../api";
import { STEPS, reachable } from "../wizard";

type Props = {
  current: number;
  job: Job | null;
  onSelect: (index: number) => void;
};

export function StepNav({ current, job, onSelect }: Props) {
  return (
    <nav aria-label="Ad workflow">
      <ol className="steps">
        {STEPS.map((step, index) => {
          const allowed = reachable(index, job);
          const state = index === current ? "current" : index < current ? "done" : "todo";
          return (
            <li key={step.id} className={state}>
              <button
                type="button"
                disabled={!allowed}
                aria-current={index === current ? "step" : undefined}
                onClick={() => onSelect(index)}
                title={allowed ? undefined : "Complete the previous step first"}
              >
                <span className="step-number">
                  {index + 1}
                </span>{" "}
                <span className="step-label">{step.label}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

const JOB_STATES = ["CREATED", "PLANNING", "GENERATING", "EVALUATING", "FINALIZED"];

/** The job lifecycle, for the progress step. */
export function Stepper({ state }: { state: string }) {
  return (
    <ol className="stepper" aria-label="Job progress">
      {JOB_STATES.map((step) => {
        const done = JOB_STATES.indexOf(step) < JOB_STATES.indexOf(state);
        const current = step === state;
        return (
          <li
            key={step}
            className={current ? "current" : done ? "done" : ""}
            aria-current={current ? "step" : undefined}
          >
            {step}
          </li>
        );
      })}
      {state === "FAILED" ? <li className="failed">FAILED</li> : null}
    </ol>
  );
}
