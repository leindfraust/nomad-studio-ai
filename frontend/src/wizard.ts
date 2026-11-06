import type { Job, Providers } from "./api";

export const STEPS = [
  { id: "brief", label: "Brief" },
  { id: "strategy", label: "Strategy" },
  { id: "storyboard", label: "Storyboard" },
  { id: "produce", label: "Produce" },
  { id: "progress", label: "Progress" },
  { id: "review", label: "Review" },
] as const;

export const STEP_COUNT = STEPS.length;

/** A step is reachable only when the data behind it exists. */
export function reachable(index: number, job: Job | null): boolean {
  if (index === 0) return true;
  if (!job) return false;
  if (index === 1) return Boolean(job.strategy);
  if (index === 2) return Boolean(job.strategy && job.storyboard);
  if (index === 3) return Boolean(job.storyboard);
  if (index === 4) return Boolean(job.storyboard) && !job.dry_run;
  return job.state === "FINALIZED" || job.state === "FAILED";
}

export function isTerminal(job: Job | null): boolean {
  return job?.state === "FINALIZED" || job?.state === "FAILED";
}

/** Video spend for the planned cut, at the configured resolution. */
export function videoCost(job: Job | null, providers: Providers | null): number {
  if (!job?.storyboard || !providers) return 0;
  const rate = providers.video.price_per_second_usd[providers.video_resolution] ?? 0;
  const seconds = job.storyboard.scenes.reduce((total, scene) => total + scene.duration_s, 0);
  return rate * seconds;
}

export function imageCost(job: Job | null): number {
  return (job?.storyboard?.scenes.length ?? 0) * 0.0035;
}
