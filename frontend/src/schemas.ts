import { z } from "zod";

/** Runtime schemas for everything the API returns. No unchecked casts. */

export const BriefSchema = z.object({
  brand: z.string(),
  category: z.string(),
  audience: z.string(),
  problem: z.array(z.string()),
  core_message: z.string(),
  brand_feel: z.array(z.string()),
  duration_s: z.number().int().min(10).max(15),
  aspect_ratio: z.literal("9:16"),
  cta: z.string(),
  visual_notes: z.string().nullable(),
});
export type Brief = z.infer<typeof BriefSchema>;

export const AngleSchema = z.object({
  name: z.string(),
  target_emotion: z.string(),
  hook: z.string(),
  concept: z.string(),
  cta: z.string(),
});

export const AngleScoreSchema = z.object({
  angle_name: z.string(),
  insight_strength: z.number(),
  hook_viability: z.number(),
  message_fidelity: z.number(),
  reason: z.string(),
});

export const StrategySchema = z.object({
  angles: z.array(AngleSchema),
  scores: z.array(AngleScoreSchema),
  selected_name: z.string(),
  selection_reason: z.string(),
  audience_insight: z.string(),
});

export const SceneSchema = z.object({
  index: z.number(),
  start_s: z.number(),
  duration_s: z.number(),
  beat: z.enum(["hook", "tension", "relief"]),
  description: z.string(),
  camera: z.string(),
  on_screen_text: z.string().nullable(),
  universal_prompt: z.string(),
  provider_prompt: z.string(),
});

export const StoryboardSchema = z.object({
  target_duration_s: z.number(),
  total_duration_s: z.number(),
  aspect_ratio: z.string(),
  continuity_block: z.string(),
  scenes: z.array(SceneSchema),
});

export const ShotSchema = z.object({
  index: z.number(),
  task: z
    .object({ provider: z.string(), model: z.string(), task_id: z.string() })
    .nullable(),
  state: z.string(),
  attempts: z.array(z.object({ at: z.string(), action: z.string(), outcome: z.string() })),
  local_path: z.string().nullable(),
  error: z.string().nullable(),
});

export const TechReportSchema = z.object({
  file: z.string(),
  duration_s: z.number(),
  width: z.number(),
  height: z.number(),
  aspect_ratio: z.string(),
  codec: z.string().nullable(),
  has_audio: z.boolean(),
  decodable: z.boolean(),
  failures: z.array(z.string()),
  valid: z.boolean(),
});

export const CreativeReportSchema = z.object({
  model: z.string(),
  scores: z.record(z.string(), z.number()),
  justifications: z.record(z.string(), z.string()),
  defects: z.array(z.string()),
  frames: z.array(z.string()),
  passed: z.boolean(),
});

export const QAReportSchema = z.object({
  technical: TechReportSchema,
  creative: CreativeReportSchema.nullable(),
  passed: z.boolean(),
  reasons: z.array(z.string()),
});

export const JobSchema = z.object({
  id: z.string(),
  state: z.string(),
  substep: z.string(),
  revision: z.number(),
  dry_run: z.boolean(),
  placeholder: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
  brief: BriefSchema,
  strategy: StrategySchema.nullable(),
  storyboard: StoryboardSchema.nullable(),
  voiceover: z
    .object({
      text: z.string(),
      start_s: z.number(),
      voice_id: z.string(),
      local_path: z.string(),
      duration_s: z.number(),
      cost_usd: z.number().nullable(),
    })
    .nullable(),
  shots: z.array(ShotSchema),
  qa: QAReportSchema.nullable(),
  error: z.record(z.string(), z.unknown()).nullable(),
  costs: z.object({ estimated_usd: z.number(), reported_usd: z.number().nullable() }),
});
export type Job = z.infer<typeof JobSchema>;

export const JobSummarySchema = z.object({
  record: JobSchema,
  stalled: z.boolean(),
});
export type JobSummary = z.infer<typeof JobSummarySchema>;

export const ProvidersSchema = z.object({
  capabilities: z.record(z.string(), z.object({ provider: z.string(), model: z.string() })),
  video_resolution: z.string(),
  video: z.object({
    model: z.string(),
    durations_s: z.array(z.number()),
    ratios: z.array(z.string()),
    resolutions: z.array(z.string()),
    supports_first_frame: z.boolean(),
    price_per_second_usd: z.record(z.string(), z.number()),
    verified: z.boolean(),
  }),
  policy: z.record(z.string(), z.unknown()),
});
export type Providers = z.infer<typeof ProvidersSchema>;
