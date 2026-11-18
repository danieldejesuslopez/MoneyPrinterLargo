/** Clips API client — catalogue, learned trends, sources and live runs. */

const ROOT = (import.meta.env.VITE_API_BASE as string) || "";
const BASE = `${ROOT}/api/clips`;

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...opts?.headers },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // keep statusText
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const CLIP_STATUSES = ["draft", "approved", "rejected", "published"] as const;
export type ClipStatus = (typeof CLIP_STATUSES)[number];
export const CLIP_LAYOUTS = ["blur", "crop", "facecam"] as const;
export type ClipLayout = (typeof CLIP_LAYOUTS)[number];

export interface ClipYouTubeUpload {
  status: "uploading" | "uploaded" | "failed" | "pending_confirmation";
  channel_id: string;
  channel_nickname: string;
  url?: string;
  error?: string;
  title?: string;
  description?: string;
  started_at?: string;
  finished_at?: string;
}

export interface ClipUploadChannel {
  id: string;
  nickname: string;
  firefox_profile: string;
  profile_ready: boolean;
}

export interface ClipUploadParams {
  channel_id: string;
  title: string;
  description: string;
}

export interface ClipUploadJob {
  job_id: string;
  stream_url: string;
}

export interface ClipUploadResolution {
  action: "published" | "retry";
  url?: string;
}

export interface Clip {
  id: string;
  title: string;
  status: ClipStatus;
  created_at: string;
  start: number;
  end: number;
  duration: number;
  score: number | null;
  picked_by: "auto" | "manual" | string;
  layout: string;
  words_from: string;
  transcript: string;
  source_id: string;
  source_title: string;
  source_url: string;
  source_target?: string;
  source_uploader: string;
  rights_note: string;
  probe: { width?: number; height?: number; duration?: number; has_audio?: number };
  available: boolean;
  size_bytes: number;
  youtube_upload?: ClipYouTubeUpload | null;
  sequence_index?: number;
  sequence_total?: number;
  clip_seconds?: number;
}

export interface TraitStats {
  winner_share: number;
  baseline_share: number;
  lift: number;
  support: number;
  meaningful: boolean;
}

export interface TrendProfile {
  ready: boolean;
  reason?: string;
  sample_size: number;
  exemplars?: number;
  baseline?: number;
  channels?: string[];
  duration_seconds?: { p25?: number; median?: number; p75?: number };
  title_words?: { median?: number };
  title_traits?: Record<string, TraitStats>;
}

export interface TrendsResponse {
  profile: TrendProfile;
  brief: string;
  window: [number, number];
}

export interface ClipSource {
  id: string;
  title: string;
  url: string;
  uploader: string;
  duration_seconds: number;
  captions_kind: string;
  rights_note: string;
  fetched_at: string;
  target: string;
  sequence?: {
    total_parts?: number;
    completed_parts?: number;
    remaining_parts?: number;
    next_start?: number;
    complete?: boolean;
    clip_seconds?: number;
  };
}

export interface ClipRunParams {
  target: string;
  mode: "auto" | "cut" | "full";
  spans?: string;
  titles?: string;
  count?: number;
  rights?: string;
  language?: string;
  layout?: ClipLayout | "";
  facecam?: string;
  subtitles?: boolean;
  whisper?: boolean;
  useLlm?: boolean;
  clipSeconds?: number;
  batchSize?: number;
  resume?: boolean;
}

export const clipsApi = {
  list: (status = "") =>
    request<Clip[]>(status ? `?status=${encodeURIComponent(status)}` : ""),
  setStatus: (id: string, status: ClipStatus) =>
    request<Clip>(`/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  remove: (id: string) =>
    request<{ ok: boolean; deleted: string[] }>(`/${id}`, { method: "DELETE" }),
  rawUrl: (id: string) => `${BASE}/${id}/raw`,
  trends: (refresh = false) =>
    request<TrendsResponse>(`/meta/trends${refresh ? "?refresh=true" : ""}`),
  sources: () => request<ClipSource[]>("/meta/sources"),
  uploadChannels: () => request<ClipUploadChannel[]>("/meta/upload-channels"),
  async upload(id: string, params: ClipUploadParams): Promise<ClipUploadJob> {
    const job = await request<ClipUploadJob>(`/${encodeURIComponent(id)}/upload`, {
      method: "POST",
      body: JSON.stringify(params),
    });
    return {
      ...job,
      stream_url: job.stream_url.startsWith("/") ? `${ROOT}${job.stream_url}` : job.stream_url,
    };
  },
  resolveUpload: (id: string, params: ClipUploadResolution) =>
    request<Clip>(`/${encodeURIComponent(id)}/upload-resolution`, {
      method: "POST",
      body: JSON.stringify(params),
    }),

  /** SSE URL for a live clips job (used by ProgressDialog). */
  runUrl(p: ClipRunParams): string {
    const qs = new URLSearchParams();
    qs.set("target", p.target.trim());
    qs.set("mode", p.mode);
    if (p.mode === "cut") {
      qs.set("spans", p.spans || "");
      if (p.titles) qs.set("titles", p.titles);
    } else if (p.mode === "full") {
      qs.set("clip_seconds", String(p.clipSeconds ?? 60));
      qs.set("batch_size", String(p.batchSize ?? 0));
      qs.set("resume", String(p.resume ?? true));
    } else {
      qs.set("count", String(p.count ?? 3));
      if (p.useLlm === false) qs.set("use_llm", "false");
    }
    if (p.rights) qs.set("rights", p.rights.trim());
    if (p.language) qs.set("language", p.language);
    if (p.layout) qs.set("layout", p.layout);
    if (p.layout === "facecam" && p.facecam) qs.set("facecam", p.facecam.trim());
    if (p.subtitles === false) qs.set("subtitles", "false");
    if (p.whisper) qs.set("whisper", "true");
    return `${BASE}/run?${qs.toString()}`;
  },
};

export function formatClock(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
