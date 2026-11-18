const BASE = (import.meta.env.VITE_API_BASE as string) || "";
export const gtaUrl = (path = "") => `${BASE}/api/gta6${path}`;

export async function gtaRequest<T = unknown>(
  path = "",
  body?: unknown,
  method = "POST",
): Promise<T> {
  const response = await fetch(
    gtaUrl(path),
    body === undefined
      ? undefined
      : {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : JSON.stringify(error.detail || response.statusText),
    );
  }
  return response.json();
}

export interface GtaSource {
  id: string;
  name: string;
  url: string;
  kind: string;
  monitor_url?: string;
  monitor_enabled?: boolean;
  credit: string;
  permission: string;
  permission_note: string;
  active: boolean;
}
export interface GtaFact {
  id: string;
  text: string;
  url: string;
  status: string;
  verified_at: string;
  note: string;
  fresh: boolean;
}
export interface GtaIdea {
  id: string;
  title: string;
  pillar: string;
  angle: string;
  fact_ids: string[];
  used: boolean;
  origin: string;
}
export interface GtaPost {
  id: string;
  title: string;
  text: string;
  platform: string;
  pillar: string;
  format: string;
  source_id: string;
  fact_ids: string[];
  clip_id: string;
  hook: string;
  commentary: string;
  spoiler: boolean;
  synthetic: boolean;
  reviewed: boolean;
  media_url: string;
  length_sec: number;
  status: string;
  scheduled_at: string;
  published_at?: string;
  published_url?: string;
  created_at: string;
  issues: string[];
  buffer_note?: string;
  buffer_post_id?: string;
  blocked_reason?: string;
  automation_approval?: string;
}
export interface GtaSettings {
  name: string;
  handle: string;
  timezone: string;
  language: string;
  platforms: string[];
  angle: string;
  bio: string;
  voice: string;
  voice_mode: string;
  avoid_creators: string;
  approval_hours: string;
  daily_x: number;
  daily_video: number;
  slots: string[];
  buffer_channels: Record<string, string>;
  launch_date: string;
  launch_fact_id: string;
  kickoff_start: string;
  launch_mode: boolean;
  youtube_channel_id: string;
  hermes_youtube_channel_ids: string[];
  hermes_routes: Record<string, {youtube_channel_id: string; language: string; source_language?: string}>;
  hermes_layout: "crop" | "facecam";
  hermes_platforms: string[];
  hermes_community_enabled: boolean;
  hermes_min_seconds: number;
  hermes_max_seconds: number;
  batch_auto_upload: boolean;
}
export interface GtaSnapshot {
  settings: GtaSettings;
  sources: GtaSource[];
  facts: GtaFact[];
  ideas: GtaIdea[];
  posts: GtaPost[];
  metrics: {
    post_id: string;
    hours: number;
    views: number | null;
    [key: string]: unknown;
  }[];
  lessons: { id: string; kind: string; text: string; evidence: string }[];
  experiments: {
    id: string;
    name: string;
    variable: string;
    control: string;
    variant: string;
    status: string;
    result: string;
  }[];
  references: {
    id: string;
    name: string;
    url: string;
    formats: string;
    hooks: string;
    lesson: string;
  }[];
  engagement: { id: string; question: string; answer: string; url: string }[];
  takedowns: {
    id: string;
    source_id: string;
    reason: string;
    action: string;
  }[];
  clip_jobs?: {
    id: string;
    status: string;
    error: string;
    clip_ids: string[];
  }[];
  news_jobs: {
    id: string;
    channel_id: string;
    status: string;
    channel_name: string;
    auto_upload: boolean;
    error: string;
    text_post_ids: string[];
    items: {
      key: string;
      fact_id: string;
      status: string;
      clip_id: string;
      post_id: string;
      error: string;
    }[];
  }[];
  checks: Record<string, boolean>;
  today: string;
  buffer_configured: boolean;
  countdown: {
    days: number | null;
    note?: string;
    date?: string;
    timezone?: string;
  };
  meta: {
    pillars: Record<string, string>;
    platforms: string[];
    routines: string[][];
    kickoff: string[];
  };
  review: {
    sample: number;
    read_hours: number | null;
    note: string;
    top: (GtaPost & { views: number; read_hours: number })[];
    bottom: (GtaPost & { views: number; read_hours: number })[];
    medians: {
      dimension: string;
      value: string;
      median: number;
      sample: number;
    }[];
    due: { post_id: string; title: string; hours: number }[];
  };
}

export const NETWORKS: Record<string, string> = {
  x: "X",
  instagram: "Instagram",
  tiktok: "TikTok",
  youtube: "YouTube Shorts",
  threads: "Threads",
  bluesky: "Bluesky",
};
export const STATUS: Record<string, string> = {
  draft: "Borrador",
  approved: "Aprobado",
  planned: "Horario reservado",
  scheduled: "En Buffer",
  published: "Publicado",
  blocked: "Bloqueado",
  pending_confirmation: "Revisar Buffer",
  dispatching: "Enviando a Buffer",
  removed: "Retirado",
};
export const apiRoot = BASE;
