import { z } from "zod";
import {
  BriefSchema,
  JobSummarySchema,
  ProvidersSchema,
  type Brief,
  type Job,
  type JobSummary,
  type Providers,
} from "./schemas";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const raw: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof raw === "object" && raw !== null && "detail" in raw
        ? String((raw as { detail: unknown }).detail)
        : response.statusText;
    throw new ApiError(response.status, detail);
  }
  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    throw new ApiError(response.status, `Unexpected response shape: ${parsed.error.message}`);
  }
  return parsed.data;
}

export const api = {
  getBrief: () => request("/api/brief", BriefSchema),
  saveBrief: (brief: Brief) =>
    request("/api/brief", BriefSchema, { method: "PUT", body: JSON.stringify(brief) }),
  providers: () => request("/api/providers", ProvidersSchema),
  listJobs: () => request("/api/jobs", z.array(JobSummarySchema)),
  getJob: (id: string) => request(`/api/jobs/${id}`, JobSummarySchema),
  createJob: (brief: Brief, dryRun: boolean) =>
    request("/api/jobs", z.object({ id: z.string(), state: z.string() }), {
      method: "POST",
      body: JSON.stringify({ brief, dry_run: dryRun }),
    }),
  resumeJob: (id: string) =>
    request(`/api/jobs/${id}/resume`, z.object({ id: z.string(), state: z.string() }), {
      method: "POST",
      body: JSON.stringify({}),
    }),
  produceJob: (id: string) =>
    request(`/api/jobs/${id}/produce`, z.object({ id: z.string(), state: z.string() }), {
      method: "POST",
      body: JSON.stringify({}),
    }),
};

export type { Brief, Job, JobSummary, Providers };
