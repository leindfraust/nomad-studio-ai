import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { TestEventSource } from "./test-setup";

const brief = {
  brand: "NomadGuard",
  category: "travel protection",
  audience: "frequent travellers aged 25-40",
  problem: ["flight delays", "cancellations"],
  core_message: "Travel with confidence knowing that you are protected when things go wrong.",
  brand_feel: ["modern", "premium"],
  duration_s: 12,
  aspect_ratio: "9:16",
  cta: "Travel protected with NomadGuard.",
  visual_notes: "Airport at dusk.",
};

const providers = {
  capabilities: {
    text: { provider: "minimax", model: "MiniMax-M3.1-Flash" },
    vision: { provider: "minimax", model: "MiniMax-M3.1-Flash" },
    image: { provider: "minimax", model: "image-01" },
    video: { provider: "minimax", model: "MiniMax-H3-Lite" },
    voice: { provider: "minimax", model: "speech-2.8-turbo" },
  },
  video_resolution: "768P",
  video: {
    model: "MiniMax-H3-Lite",
    durations_s: [4, 15],
    ratios: ["9:16"],
    resolutions: ["480P", "768P"],
    supports_first_frame: true,
    price_per_second_usd: { "480P": 0.025, "768P": 0.04 },
    verified: false,
  },
  policy: {},
};

const strategy = {
  angles: [
    { name: "Composure", target_emotion: "calm", hook: "Expect the unexpected.", concept: "c1", cta: "cta" },
    { name: "Second Cost", target_emotion: "cost", hook: "h2", concept: "c2", cta: "cta" },
    { name: "Checkmate", target_emotion: "control", hook: "h3", concept: "c3", cta: "cta" },
  ],
  scores: [
    { angle_name: "Composure", insight_strength: 9, hook_viability: 8, message_fidelity: 8, reason: "r" },
    { angle_name: "Second Cost", insight_strength: 7, hook_viability: 8, message_fidelity: 9, reason: "r" },
    { angle_name: "Checkmate", insight_strength: 5, hook_viability: 5, message_fidelity: 6, reason: "r" },
  ],
  selected_name: "Composure",
  selection_reason: "Highest total, and the hook works without sound.",
  audience_insight: "Travellers fear losing control more than the delay itself.",
};

const storyboard = {
  target_duration_s: 12,
  total_duration_s: 12,
  aspect_ratio: "9:16",
  continuity_block: "charcoal coat, navy case, dusk terminal",
  scenes: [
    {
      index: 0,
      start_s: 0,
      duration_s: 4,
      beat: "hook",
      description: "She looks up as the board changes.",
      camera: "slow push",
      on_screen_text: "Expect the unexpected.",
      universal_prompt: "u",
      provider_prompt: "p",
    },
    {
      index: 1,
      start_s: 4,
      duration_s: 4,
      beat: "tension",
      description: "She stops mid-stride.",
      camera: "handheld",
      on_screen_text: null,
      universal_prompt: "u",
      provider_prompt: "p",
    },
    {
      index: 2,
      start_s: 8,
      duration_s: 4,
      beat: "relief",
      description: "She exhales and walks on.",
      camera: "pull back",
      on_screen_text: "Travel protected.",
      universal_prompt: "u",
      provider_prompt: "p",
    },
  ],
};

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1",
    state: "PLANNING",
    substep: "strategy",
    revision: 1,
    dry_run: true,
    placeholder: false,
    created_at: "2026-11-06T00:00:00Z",
    updated_at: "2026-11-06T00:00:01Z",
    brief,
    strategy: null,
    storyboard: null,
    voiceover: null,
    shots: [],
    qa: null,
    error: null,
    costs: { estimated_usd: 0, reported_usd: null },
    ...overrides,
  };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

type Route = (init?: RequestInit) => Response | Promise<Response>;

function installFetch(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const handler = routes[`${init?.method ?? "GET"} ${url}`] ?? routes[url];
    if (!handler) throw new Error(`no mock for ${init?.method ?? "GET"} ${url}`);
    return await handler(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function calls(fetchMock: ReturnType<typeof installFetch>, prefix: string): number {
  return fetchMock.mock.calls.filter(([input, init]) => {
    const url = typeof input === "string" ? input : input.toString();
    return `${init?.method ?? "GET"} ${url}`.startsWith(prefix);
  }).length;
}

beforeEach(() => {
  TestEventSource.instances = [];
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function plannedRoutes(extra: Record<string, Route> = {}) {
  return {
    "GET /api/brief": () => json(brief),
    "GET /api/providers": () => json(providers),
    "PUT /api/brief": () => json(brief),
    "POST /api/jobs": () => json({ id: "job-1", state: "CREATED" }, 202),
    "GET /api/jobs/job-1": () => json({ record: job(), stalled: false }),
    ...extra,
  };
}

describe("wizard", () => {
  it("plans once, with the brief, from step one", async () => {
    const fetchMock = installFetch(plannedRoutes());
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "Plan the ad" }));

    await waitFor(() => expect(calls(fetchMock, "POST /api/jobs")).toBe(1));
    const [, init] = fetchMock.mock.calls.find(([, i]) => i?.method === "POST") ?? [];
    expect(String(init?.body)).toContain('"dry_run":true');
    expect(calls(fetchMock, "PUT /api/brief")).toBe(1);
  });

  it("moves to strategy when the plan arrives, and gates the next step", async () => {
    installFetch(plannedRoutes());
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Plan the ad" }));

    const stream = TestEventSource.instances.at(-1);
    expect(stream).toBeDefined();
    stream?.emit({
      record: job({ strategy: null, storyboard: null, substep: "storyboard" }),
      stalled: false,
    });
    expect(screen.queryByText("Composure")).not.toBeInTheDocument();

    stream?.emit({ record: job({ strategy, storyboard }), stalled: false });

    await waitFor(() => expect(screen.getAllByText("Composure").length).toBeGreaterThan(0));
    expect(screen.getByText(/Highest total, and the hook works/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("produces from the planned job exactly once", async () => {
    const fetchMock = installFetch(
      plannedRoutes({
        "POST /api/jobs/job-1/produce": () => json({ id: "job-1", state: "producing" }, 202),
      }),
    );
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Plan the ad" }));
    TestEventSource.instances.at(-1)?.emit({ record: job({ strategy, storyboard }), stalled: false });

    await userEvent.click(await screen.findByRole("button", { name: "4 Produce" }));
    await userEvent.click(await screen.findByRole("button", { name: "Produce the ad" }));

    await waitFor(() => expect(calls(fetchMock, "POST /api/jobs/job-1/produce")).toBe(1));
  });

  it("lets you go back to the brief after planning", async () => {
    installFetch(plannedRoutes());
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Plan the ad" }));
    TestEventSource.instances.at(-1)?.emit({ record: job({ strategy, storyboard }), stalled: false });

    await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByRole("button", { name: "Plan the ad" })).toBeInTheDocument();
    // and it must stay there, not bounce forward again
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByRole("button", { name: "Plan the ad" })).toBeInTheDocument();
  });

  it("keeps the plan button spinning until the storyboard actually arrives", async () => {
    installFetch(plannedRoutes());
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /Plan the ad/ }));

    // The POST resolved and the job exists, but the model is still writing.
    const stream = TestEventSource.instances.at(-1);
    stream?.emit({ record: job({ substep: "strategy" }), stalled: false });

    const pending = await screen.findByRole("button", { name: /Plan the ad/ });
    expect(pending).toHaveAttribute("aria-busy", "true");
    expect(pending).toBeDisabled();
    expect(pending.querySelector(".spinner")).not.toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent(/Writing three angles/);

    stream?.emit({ record: job({ strategy, storyboard }), stalled: false });

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Plan the ad/ })).not.toBeInTheDocument(),
    );
  });

  it("clears the pending state when planning fails", async () => {
    installFetch(plannedRoutes());
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /Plan the ad/ }));

    TestEventSource.instances.at(-1)?.emit({
      record: job({ state: "FAILED", dry_run: true, error: { class: "InsufficientBalance", message: "no balance" } }),
      stalled: false,
    });

    const button = await screen.findByRole("button", { name: /Plan the ad/ });
    expect(button).not.toHaveAttribute("aria-busy");
    expect(button).toBeEnabled();
  });

  it("shows the video and the gate verdict on the review step", async () => {
    installFetch(plannedRoutes());
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Plan the ad" }));

    const finished = job({
      state: "FAILED",
      substep: "failed",
      dry_run: false,
      strategy,
      storyboard,
      qa: {
        technical: {
          file: "final.mp4",
          duration_s: 12,
          width: 768,
          height: 1344,
          aspect_ratio: "9:16",
          codec: "h264",
          has_audio: true,
          decodable: true,
          failures: [],
          valid: true,
        },
        creative: {
          model: "MiniMax-M3.1-Flash",
          scores: {
            realism: 7,
            visual_consistency: 8,
            brief_adherence: 5,
            hook_strength: 6,
            advertising_quality: 6,
            storyboard_fidelity: 3,
          },
          justifications: {},
          defects: [],
          frames: [],
          passed: false,
        },
        passed: false,
        reasons: [],
      },
      error: null,
    });
    TestEventSource.instances.at(-1)?.emit({ record: finished, stalled: false });

    const video = await screen.findByRole("heading", { name: /Review/ });
    expect(video).toBeInTheDocument();
    expect(screen.getByText(/^Not shipped/)).toBeInTheDocument();
    expect(screen.getByText("The brief you gave it")).toBeInTheDocument();
  });
});
