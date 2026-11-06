import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type Brief, type Job, type Providers } from "./api";
import { JobSummarySchema } from "./schemas";
import { StepNav } from "./components/StepNav";
import { Button } from "./components/Button";
import { BriefStep, StoryboardStep, StrategyStep } from "./components/PlanSteps";
import { ProduceStep, ProgressStep, ReviewStep } from "./components/RunSteps";
import { isTerminal, reachable } from "./wizard";

function message(error: unknown): string {
  if (error instanceof ApiError) return `${error.status}: ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

const TITLES = ["Brief", "Strategy", "Storyboard", "Produce", "Generating", "Review"];

export default function App() {
  const [step, setStep] = useState(0);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [providers, setProviders] = useState<Providers | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(false);
  const topRef = useRef<HTMLDivElement>(null);
  const advanced = useRef<string | null>(null);

  const loadConfig = useCallback(async () => {
    setError(null);
    setLoadingConfig(true);
    try {
      const [loadedBrief, loadedProviders] = await Promise.all([api.getBrief(), api.providers()]);
      setBrief(loadedBrief);
      setProviders(loadedProviders);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setLoadingConfig(false);
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  useEffect(() => {
    if (!jobId) return;
    let disposed = false;
    void api
      .getJob(jobId)
      .then((summary) => {
        if (!disposed) setJob(summary.record);
      })
      .catch((failure) => setError(message(failure)));

    const source = new EventSource(`/api/jobs/${jobId}/events`);
    source.onmessage = (event) => {
      const parsed = JobSummarySchema.safeParse(JSON.parse(event.data));
      if (!parsed.success) {
        setError("Unexpected job event shape.");
        return;
      }
      setJob(parsed.data.record);
      if (isTerminal(parsed.data.record)) source.close();
    };
    source.onerror = () => source.close();
    return () => {
      disposed = true;
      source.close();
    };
  }, [jobId]);

  useEffect(() => {
    if (!job) return;
    // Advance once per transition, never on every render: otherwise going back to
    // an earlier step is impossible, because the effect pushes you forward again.
    const planKey = `${job.id}:plan`;
    const doneKey = `${job.id}:done`;
    if (job.strategy && job.storyboard && step === 0 && advanced.current !== planKey) {
      advanced.current = planKey;
      setStep(1);
      return;
    }
    if (isTerminal(job) && !job.dry_run && advanced.current !== doneKey) {
      advanced.current = doneKey;
      setStep(5);
    }
  }, [job, step]);

  useEffect(() => {
    const top = topRef.current;
    // jsdom has no layout engine; only call when the platform provides it.
    if (top && typeof top.scrollIntoView === "function") {
      top.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }, [step]);

  const go = useCallback(
    (index: number) => {
      if (reachable(index, job)) setStep(index);
    },
    [job],
  );

  // The POST returns as soon as the job is queued; the model keeps working after it.
  // `busy` alone would drop the spinner in 40ms, so pending is driven by the job.
  const planPending = Boolean(jobId) && !job?.storyboard && job?.state !== "FAILED";

  async function plan() {
    if (!brief || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.saveBrief(brief);
      const created = await api.createJob(brief, true);
      setJobId(created.id);
      setJob(null);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }

  async function produce() {
    if (!jobId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.produceJob(jobId);
      setStep(4);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }

  async function resume() {
    if (!jobId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.resumeJob(jobId);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setBusy(false);
    }
  }

  function restart() {
    setJobId(null);
    setJob(null);
    setError(null);
    setStep(0);
  }

  return (
    <div className="app">
      <div ref={topRef} />
      <header>
        <h1>NomadGuard Ad Studio</h1>
        <p className="muted">Brief, strategy, storyboard, production and review — one step at a time.</p>
        {providers ? (
          <p className="muted small">
            video {providers.video.model} @ {providers.video_resolution} · text{" "}
            {providers.capabilities.text?.model ?? "?"} · voice {providers.capabilities.voice?.model ?? "?"}
          </p>
        ) : null}
      </header>

      {providers && Object.values(providers.capabilities).some((item) => item.provider === "offline") ? (
        <p className="callout warning" role="status">
          Offline providers are active. Anything produced here is labelled placeholder footage, not an ad.
        </p>
      ) : null}

      <p className="callout error" role="alert" aria-live="assertive" hidden={!error}>
        {error}
      </p>

      <StepNav current={step} job={job} onSelect={go} />

      <main className="single">
        <h2 className="step-title" id="step-title">
          <span className="muted">Step {step + 1} of 6</span> {TITLES[step]}
        </h2>

        {step === 0 && brief ? (
          <BriefStep
            brief={brief}
            busy={busy}
            pending={planPending}
            onChange={setBrief}
            onPlan={plan}
          />
        ) : null}

        {step === 0 && !brief ? (
          <section className="panel" aria-labelledby="step-title">
            <p className="callout error" role="alert">
              {loaded
                ? `The brief could not be loaded from the API. ${error ?? ""}`
                : "Loading the brief…"}
            </p>
            {loaded ? (
              <div className="actions">
                <Button label="Retry" variant="primary" loading={loadingConfig} onClick={() => void loadConfig()} />
              </div>
            ) : null}
            <p className="muted small">
              The API must be running on the same origin. Start both with <code>npm run dev</code>.
            </p>
          </section>
        ) : null}

        {step === 1 && job ? (
          <StrategyStep job={job} busy={busy} onBack={() => go(0)} onContinue={() => go(2)} />
        ) : null}

        {step === 2 && job ? (
          <StoryboardStep job={job} busy={busy} onBack={() => go(1)} onContinue={() => go(3)} />
        ) : null}

        {step === 3 && job ? (
          <ProduceStep
            job={job}
            providers={providers}
            busy={busy}
            onBack={() => go(2)}
            onProduce={produce}
          />
        ) : null}

        {step === 4 && job ? <ProgressStep job={job} /> : null}

        {step === 5 && job ? (
          <ReviewStep job={job} busy={busy} onRestart={restart} onResume={resume} />
        ) : null}

        {!job && step > 0 ? (
          <section className="panel">
            <p className="muted" role="status">
              Waiting for the plan…
            </p>
          </section>
        ) : null}
      </main>
    </div>
  );
}
