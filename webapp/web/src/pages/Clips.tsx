import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Scissors,
  Play,
  Check,
  X,
  Trash2,
  RefreshCw,
  Link2,
  Film,
  Loader2,
  Upload,
  ExternalLink,
  ListVideo,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/layout/Header";
import { PageShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ProgressDialog } from "@/components/ProgressDialog";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { cn } from "@/lib/utils";
import {
  clipsApi,
  formatClock,
  type Clip,
  type ClipLayout,
  type ClipSource,
  type ClipStatus,
  type ClipUploadChannel,
  type TrendsResponse,
} from "@/lib/clips";
import {
  Divider,
  Field,
  GenerateMetric,
  Mini,
  Row,
  Segmented,
  TextArea,
  TextInput,
  ToggleRow,
  TrackHeader,
} from "./Generate";

// Spanish labels for the title traits the trend profile measures
// (src/clips/trends.py TRAIT_LABELS).
const TRAIT_LABELS: Record<string, string> = {
  question: "título en forma de pregunta",
  has_number: "un número concreto en el título",
  has_hashtag: "hashtags al final del título",
  has_colon: "título «Tema: remate»",
  second_person: "hablarle directamente al espectador",
  superlative: "un superlativo (más, nunca, único)",
  short_title: "título de menos de 8 palabras",
  long_title: "título largo, de 13+ palabras",
  shouty: "mayúsculas marcadas",
};

const STATUS_META: Record<ClipStatus, { label: string; colorVar: string }> = {
  draft: { label: "Borrador", colorVar: "var(--muted-foreground)" },
  approved: { label: "Aprobado", colorVar: "var(--success)" },
  rejected: { label: "Rechazado", colorVar: "var(--destructive)" },
  published: { label: "Publicado", colorVar: "var(--primary)" },
};

const SPAN_RE = /^\d+(?::\d{1,2}){0,2}(?:\.\d+)?\s*-\s*\d+(?::\d{1,2}){0,2}(?:\.\d+)?$/;

type WordsSource = "captions" | "whisper" | "none";

function isUrl(value: string) {
  return /^https?:\/\//i.test(value.trim());
}

function uploadBlocked(clip: Clip) {
  return !clip.available || clip.status === "rejected" || clip.status === "published"
    || ["uploading", "uploaded", "pending_confirmation"].includes(clip.youtube_upload?.status || "");
}

function continuationTarget(clip: Clip, sources: ClipSource[]) {
  return clip.source_target || sources.find((source) => source.id === clip.source_id)?.target || clip.source_url || "";
}

function canContinueClip(clip: Clip, sources: ClipSource[]) {
  return !!continuationTarget(clip, sources)
    && !sources.find((source) => source.id === clip.source_id)?.sequence?.complete;
}

function defaultUploadDescription(clip: Clip) {
  return [
    clip.title,
    clip.transcript?.trim().slice(0, 3500),
    clip.source_url ? `Fuente: ${clip.source_url}` : clip.source_title,
    "#Shorts",
  ].filter(Boolean).join("\n\n").slice(0, 5000);
}

function safeYouTubeUrl(url: string | undefined) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && (parsed.hostname === "youtu.be"
      || parsed.hostname === "youtube.com" || parsed.hostname.endsWith(".youtube.com")) ? parsed.href : null;
  } catch {
    return null;
  }
}

function youtubeVideoUrl(value: string) {
  const safe = safeYouTubeUrl(value.trim());
  if (!safe) return null;
  const parsed = new URL(safe);
  const id = parsed.hostname === "youtu.be" ? parsed.pathname.slice(1)
    : ["youtube.com", "www.youtube.com", "m.youtube.com"].includes(parsed.hostname)
    ? parsed.pathname === "/watch" ? parsed.searchParams.get("v") || ""
      : /^\/(shorts|live)\//.test(parsed.pathname) ? parsed.pathname.split("/")[2] || "" : ""
    : "";
  return /^[A-Za-z0-9_-]{11}$/.test(id) ? safe : null;
}

/** "1:32-1:58 | TÍTULO" per line → spans + titles kept in the same order. */
function parseSpanLines(text: string) {
  const spans: string[] = [];
  const titles: string[] = [];
  const bad: string[] = [];
  for (const raw of text.split(/\n+/)) {
    const line = raw.trim();
    if (!line) continue;
    const [spanPart, ...titleParts] = line.split("|");
    const span = spanPart.trim().replace(/\s+/g, "");
    if (!SPAN_RE.test(span)) {
      bad.push(line);
      continue;
    }
    spans.push(span);
    titles.push(titleParts.join("|").trim());
  }
  return { spans, titles, bad };
}

export function Clips({ initialMode = "auto" }: { initialMode?: "auto" | "cut" | "full" }) {
  // Source
  const [target, setTarget] = useState("");
  const [language, setLanguage] = useState("");
  // Cut
  const [mode, setMode] = useState<"auto" | "cut" | "full">(initialMode);
  const [count, setCount] = useState("3");
  const [useLlm, setUseLlm] = useState(true);
  const [spanText, setSpanText] = useState("");
  const [wordsFrom, setWordsFrom] = useState<WordsSource>("captions");
  const [clipSeconds, setClipSeconds] = useState("60");
  const [batchSize, setBatchSize] = useState("0");
  const [resume, setResume] = useState(true);
  useEffect(() => {
    setMode(initialMode);
    if (initialMode === "full") setResume(true);
  }, [initialMode]);
  const cutFormRef = useRef<HTMLDivElement | null>(null);
  // Output
  const [layout, setLayout] = useState<ClipLayout>("blur");
  const [facecam, setFacecam] = useState("");

  const [trends, setTrends] = useState<TrendsResponse | null>(null);
  const [sources, setSources] = useState<ClipSource[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [loadingClips, setLoadingClips] = useState(true);
  const [filter, setFilter] = useState<"" | ClipStatus>("");
  const [newClipIds, setNewClipIds] = useState<string[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [revealClips, setRevealClips] = useState(false);
  const knownClipIds = useRef<Set<string> | null>(null);
  const refreshId = useRef(0);
  const catalogueRef = useRef<HTMLElement | null>(null);
  const previewTrigger = useRef<HTMLElement | null>(null);

  const [sseUrl, setSseUrl] = useState<string | null>(null);
  const [progressOpen, setProgressOpen] = useState(false);
  const [toDelete, setToDelete] = useState<Clip | null>(null);
  const [uploadClip, setUploadClip] = useState<Clip | null>(null);
  const [uploadChannels, setUploadChannels] = useState<ClipUploadChannel[]>([]);
  const [loadingUploadChannels, setLoadingUploadChannels] = useState(false);
  const [uploadChannelId, setUploadChannelId] = useState("");
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadDescription, setUploadDescription] = useState("");
  const [startingUpload, setStartingUpload] = useState(false);
  const uploadRequestPending = useRef(false);
  const [uploadProgressOpen, setUploadProgressOpen] = useState(false);
  const [uploadSseUrl, setUploadSseUrl] = useState<string | null>(null);
  const uploadJobClipId = useRef<string | null>(null);
  const [resolutionClip, setResolutionClip] = useState<Clip | null>(null);
  const [resolutionUrl, setResolutionUrl] = useState("");
  const [resolvingUpload, setResolvingUpload] = useState(false);
  const resolutionRequestPending = useRef(false);

  const refreshClips = useCallback(async (quiet = false) => {
    const requestId = ++refreshId.current;
    if (!quiet) setLoadingClips(true);
    try {
      const updated = await clipsApi.list();
      if (requestId !== refreshId.current) return;
      const added = knownClipIds.current
        ? updated.filter((clip) => !knownClipIds.current!.has(clip.id))
        : [];
      knownClipIds.current = new Set(updated.map((clip) => clip.id));
      setClips(updated);
      if (added.length) {
        setNewClipIds((prev) => [...new Set([...added.map((clip) => clip.id), ...prev])]);
        setFilter("");
        setRevealClips(true);
      }
      return updated;
    } catch (e) {
      if (!quiet) toast.error(`No se pudo leer el catálogo: ${(e as Error).message}`);
    } finally {
      if (requestId === refreshId.current) setLoadingClips(false);
    }
  }, []);

  const refreshSources = useCallback(() => {
    clipsApi.sources().then(setSources).catch(() => setSources([]));
  }, []);

  useEffect(() => {
    refreshClips();
    refreshSources();
    clipsApi.trends().then(setTrends).catch(() => setTrends(null));
    // A closed progress dialog disconnects SSE while the render keeps running.
    // Keep finished clips visible without replacing their mounted players.
    const refresh = () => {
      if (document.visibilityState === "visible") {
        void refreshClips(true);
        refreshSources();
      }
    };
    const timer = window.setInterval(refresh, 5000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
      refreshId.current++;
    };
  }, [refreshClips, refreshSources]);

  useEffect(() => {
    if (!revealClips || progressOpen) return;
    const timer = window.setTimeout(() => {
      catalogueRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      setRevealClips(false);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [revealClips, progressOpen]);

  const parsed = useMemo(() => parseSpanLines(spanText), [spanText]);
  const drafts = clips.filter((c) => c.status === "draft").length;
  const visible = filter ? clips.filter((c) => c.status === filter) : clips;
  const newClips = clips.filter((clip) => newClipIds.includes(clip.id));
  const previewClip = clips.find((clip) => clip.id === previewId);
  const windowLabel = trends ? `${trends.window[0]}–${trends.window[1]}s` : "—";
  const selectedSource = target.trim()
    ? sources.find((source) => source.target === target.trim() || source.url === target.trim())
    : undefined;
  const selectedSequence = selectedSource?.sequence;
  const uploadTarget = clips.find((clip) => clip.id === uploadClip?.id) || uploadClip;
  const uploadChannel = uploadChannels.find((channel) => channel.id === uploadChannelId);
  const hasRunningUpload = clips.some((clip) => clip.youtube_upload?.status === "uploading");
  const pendingUploads = clips.filter((clip) => clip.youtube_upload?.status === "pending_confirmation");
  const hasActiveUpload = startingUpload || resolvingUpload || hasRunningUpload
    || pendingUploads.length > 0;
  const resolutionTarget = clips.find((clip) => clip.id === resolutionClip?.id) || resolutionClip;
  const resolutionDisabled = startingUpload || hasRunningUpload || resolvingUpload;
  const confirmedVideoUrl = youtubeVideoUrl(resolutionUrl);

  function openResolution(clip: Clip) {
    if (resolutionDisabled || clip.youtube_upload?.status !== "pending_confirmation") return;
    setPreviewId(null);
    setResolutionUrl(youtubeVideoUrl(clip.youtube_upload.url || "") || "");
    setResolutionClip(clip);
  }

  async function resolveUpload(action: "published" | "retry") {
    if (resolutionRequestPending.current || resolutionDisabled
      || resolutionTarget?.youtube_upload?.status !== "pending_confirmation") return;
    if (action === "published" && !confirmedVideoUrl) return;
    resolutionRequestPending.current = true;
    setResolvingUpload(true);
    try {
      const updated = await clipsApi.resolveUpload(resolutionTarget.id, {
        action, ...(action === "published" ? { url: confirmedVideoUrl! } : {}),
      });
      refreshId.current++;
      setLoadingClips(false);
      setClips((previous) => previous.map((clip) => clip.id === updated.id ? updated : clip));
      setResolutionClip(null);
      toast.success(action === "published"
        ? "Publicación registrada en el catálogo"
        : "Reintento habilitado. Puedes volver a subir el clip.");
      void refreshClips(true);
    } catch (error) {
      toast.error((error as Error).message);
      void refreshClips(true);
    } finally {
      resolutionRequestPending.current = false;
      setResolvingUpload(false);
    }
  }

  useEffect(() => {
    if (!uploadClip) return;
    let cancelled = false;
    setLoadingUploadChannels(true);
    clipsApi.uploadChannels().then((channels) => {
      if (cancelled) return;
      setUploadChannels(channels);
      setUploadChannelId((previous) => {
        const preferred = uploadClip.youtube_upload?.channel_id || previous;
        return channels.some((channel) => channel.id === preferred)
          ? preferred : (channels.find((channel) => channel.profile_ready) || channels[0])?.id || "";
      });
    }).catch((error: Error) => {
      if (cancelled) return;
      setUploadChannels([]);
      setUploadChannelId("");
      toast.error(`No se pudieron cargar los canales: ${error.message}`);
    }).finally(() => {
      if (!cancelled) setLoadingUploadChannels(false);
    });
    return () => { cancelled = true; };
  }, [uploadClip]);

  function openUpload(clip: Clip) {
    if (uploadBlocked(clip) || hasActiveUpload) return;
    setUploadTitle((clip.youtube_upload?.title || clip.title).replace(/\s+/g, " ").trim().slice(0, 100));
    setUploadDescription(clip.youtube_upload?.description || defaultUploadDescription(clip));
    setPreviewId(null);
    setUploadClip(clip);
  }

  async function startUpload() {
    if (uploadRequestPending.current || !uploadTarget || uploadBlocked(uploadTarget) || hasActiveUpload) return;
    if (!uploadChannel?.profile_ready || !uploadTitle.trim() || !uploadDescription.trim()) return;
    const targetClip = uploadTarget;
    uploadRequestPending.current = true;
    setStartingUpload(true);
    try {
      const job = await clipsApi.upload(targetClip.id, {
        channel_id: uploadChannel.id,
        title: uploadTitle.trim(),
        description: uploadDescription.trim(),
      });
      uploadJobClipId.current = targetClip.id;
      refreshId.current++;
      setLoadingClips(false);
      setClips((previous) => previous.map((clip) => clip.id === targetClip.id ? {
        ...clip,
        youtube_upload: {
          status: "uploading", channel_id: uploadChannel.id, channel_nickname: uploadChannel.nickname,
          title: uploadTitle.trim(), description: uploadDescription.trim(),
        },
      } : clip));
      setUploadClip(null);
      setUploadSseUrl(job.stream_url);
      setUploadProgressOpen(true);
    } catch (error) {
      toast.error((error as Error).message);
      void refreshClips(true);
    } finally {
      uploadRequestPending.current = false;
      setStartingUpload(false);
    }
  }

  async function finishUpload() {
    const updated = await refreshClips(true);
    const clip = updated?.find((item) => item.id === uploadJobClipId.current);
    if (clip?.youtube_upload?.status === "pending_confirmation") {
      toast.info("La subida necesita confirmación. Revisa YouTube Studio antes de volver a intentarlo.");
    }
  }

  function openPreview(id: string) {
    previewTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setPreviewId(id);
  }

  async function finishRun() {
    refreshSources();
    await refreshClips();
    setFilter("");
    setProgressOpen(false);
    setRevealClips(true);
  }

  function pickSource(s: ClipSource) {
    setTarget(s.target);
  }

  function continueSource(source: ClipSource, duration?: number) {
    setTarget(source.target || source.url);
    setMode("full");
    setResume(true);
    const seconds = duration || source.sequence?.clip_seconds;
    if (seconds && Number.isFinite(seconds) && seconds >= 5 && seconds <= 180) {
      setClipSeconds(String(seconds));
    }
    setPreviewId(null);
    cutFormRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function continueClip(clip: Clip) {
    const source = sources.find((item) => item.id === clip.source_id);
    const nextTarget = continuationTarget(clip, sources);
    if (!nextTarget || source?.sequence?.complete) return;
    if (source) {
      continueSource({ ...source, target: nextTarget }, clip.clip_seconds);
      return;
    }
    continueSource({
      id: clip.source_id, target: nextTarget, url: clip.source_url,
      title: clip.source_title, uploader: clip.source_uploader, duration_seconds: 0,
      captions_kind: "", rights_note: clip.rights_note, fetched_at: "",
    }, clip.clip_seconds);
  }

  function start() {
    const t = target.trim();
    if (!t) {
      toast.error("Pega la URL del vídeo o la ruta de un archivo.");
      return;
    }
    if (mode === "cut") {
      if (parsed.bad.length) {
        toast.error(`Tramo no válido: «${parsed.bad[0]}». Usa 1:32-1:58.`);
        return;
      }
      if (!parsed.spans.length) {
        toast.error("Añade al menos un tramo, uno por línea: 1:32-1:58 | TÍTULO");
        return;
      }
    }
    if (mode === "full") {
      const seconds = Number(clipSeconds);
      const batch = Number(batchSize);
      if (!clipSeconds.trim() || !Number.isFinite(seconds) || seconds < 5 || seconds > 180) {
        toast.error("La duración por parte debe estar entre 5 y 180 segundos.");
        return;
      }
      if (!batchSize.trim() || !Number.isSafeInteger(batch) || batch < 0) {
        toast.error("El lote debe ser un número entero desde 0. Usa 0 para procesar todo el video.");
        return;
      }
    }
    if (layout === "facecam" && facecam.trim() && !/^\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*$/.test(facecam)) {
      toast.error("La caja de la cámara debe tener este formato: x,y,ancho,alto");
      return;
    }
    setSseUrl(
      clipsApi.runUrl({
        target: t,
        mode,
        spans: parsed.spans.join(";"),
        titles: parsed.titles.some(Boolean) ? parsed.titles.join("|") : "",
        count: Number(count),
        useLlm,
        clipSeconds: Number(clipSeconds),
        batchSize: Number(batchSize),
        resume,
        language,
        layout,
        facecam,
        subtitles: wordsFrom !== "none",
        whisper: wordsFrom === "whisper",
      }),
    );
    setProgressOpen(true);
  }

  async function setStatus(clip: Clip, status: ClipStatus) {
    try {
      const updated = await clipsApi.setStatus(clip.id, status);
      refreshId.current++;
      setLoadingClips(false);
      setClips((prev) => prev.map((c) => (c.id === clip.id ? updated : c)));
      refreshSources();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    try {
      await clipsApi.remove(toDelete.id);
      refreshId.current++;
      setLoadingClips(false);
      setClips((prev) => prev.filter((c) => c.id !== toDelete.id));
      setNewClipIds((prev) => prev.filter((id) => id !== toDelete.id));
      refreshSources();
      toast.success("Clip borrado");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setToDelete(null);
    }
  }

  return (
    <>
      <Header
        eyebrow="· estudio"
        title={initialMode === "full" ? "Clips continuados" : "Clips"}
        description={initialMode === "full"
          ? "Divide el video entero en partes y continúa desde donde quedaste"
          : "Aprende de lo que gana y recorta shorts de vídeos largos"}
        actions={
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => void refreshClips()}>
            <RefreshCw className="h-4 w-4" />
            <span className="hidden sm:inline">Actualizar</span>
          </Button>
        }
      />

      <PageShell>
        <section className="page-hero">
          <div className="page-hero-inner">
            <div>
              <span className="eyebrow block mb-2">Clip board</span>
              <h1 className="page-title">
                {initialMode === "full"
                  ? <>Crea tus <span className="brand-text">clips continuados</span></>
                  : <>Recorta el siguiente <span className="brand-text">clip</span></>}
              </h1>
              <p className="page-subtitle">
                {initialMode === "full"
                  ? "Pega un video para dividirlo en partes consecutivas hasta el final. Puedes continuar después sin repetir las partes terminadas."
                  : "Pega un vídeo, elige los momentos o deja que el módulo los encuentre, y revisa los clips 9:16 aquí mismo. Nada se publica sin que lo apruebes."}
              </p>
            </div>
            <div className="metric-strip grid-cols-3 w-full sm:w-auto sm:min-w-[460px]">
              <GenerateMetric
                label="Aprendido de"
                value={trends?.profile.ready ? `${trends.profile.sample_size} shorts` : "—"}
                colorVar="var(--primary)"
              />
              <GenerateMetric label="Duración" value={windowLabel} colorVar="var(--accent)" />
              <GenerateMetric label="Borradores" value={drafts} colorVar="var(--gold)" />
            </div>
          </div>
        </section>

        <div className="grid grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_360px] gap-3.5">
          {/* MAIN — 3-stage console */}
          <div ref={cutFormRef} className="studio-surface overflow-hidden scroll-mt-6" style={{ background: "hsl(var(--card))" }}>
            <div
              className="grid grid-cols-1 md:grid-cols-3"
              style={{ borderBottom: "1px solid hsl(var(--border) / .07)" }}
            >
              <TrackHeader num="01" label="FUENTE" desc="Qué vídeo recortar" colorVar="var(--primary)" rightBorder />
              <TrackHeader num="02" label="CORTE" desc="Cómo se eligen los momentos" colorVar="var(--accent)" rightBorder />
              <TrackHeader num="03" label="SALIDA" desc="Cómo se ve el clip" colorVar="var(--gold)" />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3">
              {/* FUENTE */}
              <div
                className="px-[18px] pt-[18px] pb-[22px] flex flex-col gap-3.5 md:border-r"
                style={{ borderColor: "hsl(var(--border) / .07)" }}
              >
                <Field label="Vídeo" hint={isUrl(target) ? "url" : target ? "archivo" : ""}>
                  <TextInput
                    value={target}
                    onChange={setTarget}
                    placeholder="https://www.youtube.com/watch?v=…"
                    mono
                  />
                </Field>
                <Field label="Idioma">
                  <Segmented
                    value={language}
                    onChange={setLanguage}
                    accentVar="var(--primary)"
                    options={[
                      { value: "", label: "Original" },
                      { value: "es", label: "Español" },
                      { value: "en", label: "Inglés" },
                    ]}
                  />
                </Field>
                {sources.length > 0 && (
                  <Field label="Recientes" hint="en caché">
                    <div className="flex flex-col gap-1.5">
                      {sources.slice(0, 4).map((s) => (
                        <div key={s.id} className="flex flex-col gap-1">
                        <button
                          type="button"
                          onClick={() => pickSource(s)}
                          className={cn(
                            "w-full text-left rounded-lg px-2.5 py-2 transition-colors",
                            target === s.target ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                          )}
                          style={{
                            background:
                              target === s.target ? "hsl(var(--primary) / .10)" : "hsl(var(--background))",
                            border: `1px solid ${
                              target === s.target ? "hsl(var(--primary) / .35)" : "hsl(var(--border) / .07)"
                            }`,
                          }}
                          title={s.target}
                        >
                          <div className="flex items-center gap-1.5 text-[12px] font-medium truncate">
                            <Link2 className="h-3 w-3 shrink-0" strokeWidth={1.7} />
                            <span className="truncate">{s.title}</span>
                          </div>
                          <div className="font-mono text-[10px] mt-0.5 truncate">
                            {s.uploader || "local"} · {formatClock(s.duration_seconds)}
                            {s.captions_kind ? ` · subs ${s.captions_kind}` : ""}
                          </div>
                          {s.sequence && (
                            <div className="text-[10.5px] mt-1">
                              {s.sequence.completed_parts ?? 0}/{s.sequence.total_parts ?? "—"} partes listas
                              {s.sequence.complete ? " · video completo" : ""}
                            </div>
                          )}
                        </button>
                        {s.sequence && !s.sequence.complete && (s.sequence.remaining_parts ?? 0) > 0 && (
                          <Button variant="ghost" size="sm" className="justify-start gap-1.5 text-xs" onClick={() => continueSource(s)}>
                            <ListVideo className="h-3.5 w-3.5" /> Continuar video
                          </Button>
                        )}
                        </div>
                      ))}
                    </div>
                  </Field>
                )}
              </div>

              {/* CORTE */}
              <div
                className="px-[18px] pt-[18px] pb-[22px] flex flex-col gap-[18px] md:border-r"
                style={{ borderColor: "hsl(var(--border) / .07)" }}
              >
                <Button asChild variant={mode === "full" ? "soft" : "outline"} className="w-full justify-start gap-2">
                  <Link to="/clips/continuados" onClick={() => { setMode("full"); setResume(true); }}>
                    <ListVideo className="h-4 w-4" /> Clips continuados
                  </Link>
                </Button>
                <Field label="Modo">
                  <Select
                    value={mode}
                    onValueChange={(v) => setMode(v as "auto" | "cut" | "full")}
                  >
                    <SelectTrigger aria-label="Modo de recorte"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">Automático</SelectItem>
                      <SelectItem value="cut">Tramos a mano</SelectItem>
                      <SelectItem value="full">Video completo</SelectItem>
                    </SelectContent>
                  </Select>
                </Field>

                {mode === "auto" ? (
                  <>
                    <Field label="Clips" hint={`ventana ${windowLabel}`}>
                      <Segmented
                        value={count}
                        onChange={setCount}
                        accentVar="var(--accent)"
                        options={["1", "2", "3", "4", "5"].map((n) => ({ value: n, label: n }))}
                      />
                    </Field>
                    <ToggleRow
                      label="Puntuar con modelo"
                      hint="El LLM juzga gancho, remate y si se entiende solo. Sin él, solo heurísticas."
                      value={useLlm}
                      onChange={setUseLlm}
                    />
                  </>
                ) : mode === "full" ? (
                  <>
                    <p className="text-[11.5px] text-muted-foreground leading-relaxed -mt-1">
                      Divide todo el video en partes consecutivas, sin saltarse tramos. La última parte incluye el final.
                    </p>
                    <Field label="Segundos por parte" hint="aproximados · 5–180">
                      <TextInput value={clipSeconds} onChange={setClipSeconds} placeholder="60" mono />
                    </Field>
                    <Field label="Partes por lote" hint="0 = todo el video">
                      <TextInput value={batchSize} onChange={setBatchSize} placeholder="0" mono />
                    </Field>
                    <ToggleRow
                      label="Reanudar sin repetir"
                      hint="Conserva las partes ya creadas y completa los tramos pendientes. Desactívalo para generar una nueva serie."
                      value={resume}
                      onChange={setResume}
                    />
                    {selectedSequence && (
                      <div className="rounded-lg bg-background/60 px-3 py-2 text-[11.5px] text-muted-foreground leading-relaxed" role="status">
                        {selectedSequence.completed_parts ?? 0} de {selectedSequence.total_parts ?? "—"} partes listas
                        {selectedSequence.complete
                          ? ". Ya se cubrió todo el video."
                          : `. Próximo tramo pendiente: ${formatClock(selectedSequence.next_start)}.`}
                        {selectedSequence.clip_seconds && Number(clipSeconds) !== selectedSequence.clip_seconds
                          ? " El progreso corresponde a la duración usada anteriormente."
                          : ""}
                      </div>
                    )}
                  </>
                ) : (
                  <Field
                    label="Tramos"
                    hint={`${parsed.spans.length} tramo${parsed.spans.length === 1 ? "" : "s"}`}
                  >
                    <TextArea
                      value={spanText}
                      onChange={setSpanText}
                      rows={5}
                      placeholder={"1:32-1:58 | CUANDO LOS AVIONES VIENEN\n8:05-8:50 | UNA PISTOLA DE GATITOS"}
                    />
                    {parsed.bad.length > 0 && (
                      <div className="text-[11px]" style={{ color: "hsl(var(--destructive))" }}>
                        No entiendo: {parsed.bad[0]}
                      </div>
                    )}
                  </Field>
                )}

                <Divider />
                <Field label="Subtítulos">
                  <Segmented
                    value={wordsFrom}
                    onChange={(v) => setWordsFrom(v as WordsSource)}
                    accentVar="var(--accent)"
                    options={[
                      { value: "captions", label: "Del vídeo" },
                      { value: "whisper", label: "Whisper" },
                      { value: "none", label: "Sin" },
                    ]}
                  />
                </Field>
                <div className="text-[11px] text-muted-foreground leading-relaxed -mt-2">
                  {wordsFrom === "captions"
                    ? "Usa los subtítulos de YouTube si existen (escritos, o el reconocimiento en el idioma original) y si no, Whisper."
                    : wordsFrom === "whisper"
                    ? "Ignora los subtítulos del vídeo y transcribe con Whisper."
                    : "Para gameplay o vídeos sin voz."}
                </div>
              </div>

              {/* SALIDA */}
              <div className="px-[18px] pt-[18px] pb-[22px] flex flex-col gap-[18px]">
                <Field label="Encuadre 9:16">
                  <Segmented
                    value={layout}
                    onChange={(v) => setLayout(v as ClipLayout)}
                    accentVar="var(--gold)"
                    options={[
                      { value: "blur", label: "Completo" },
                      { value: "crop", label: "Recorte" },
                      { value: "facecam", label: "Facecam" },
                    ]}
                  />
                </Field>
                <div className="text-[11px] text-muted-foreground leading-relaxed -mt-2">
                  {layout === "blur"
                    ? "El cuadro entero sobre un fondo desenfocado. No se pierde nada."
                    : layout === "crop"
                    ? "Recorte central. Más grande, pero se pierden los lados."
                    : "Puedes marcar la cámara a mano para ponerla arriba. Sin caja manual, solo se añade si el encuadre corta la cara."}
                </div>
                <div className="text-[11px] text-muted-foreground leading-relaxed -mt-2">
                  Solo añade la cara arriba cuando queda cortada o fuera del encuadre.
                  Si ya se ve completa, no la duplica. Los zooms del vídeo se conservan.
                </div>
                {layout === "facecam" && (
                  <Field label="Caja de la cámara" hint="Opcional · x,y,ancho,alto">
                    <TextInput value={facecam} onChange={setFacecam} placeholder="Vacío: detectar automáticamente" mono />
                  </Field>
                )}

                <div className="mt-auto pt-[18px]">
                  <Button variant="brand" size="lg" className="w-full gap-2" onClick={start}
                    disabled={mode === "full" && resume && !!selectedSequence?.complete}>
                    <Scissors className="h-3.5 w-3.5" />
                    {mode === "full" ? resume && selectedSequence?.complete ? "Video completo"
                      : resume && (selectedSequence?.completed_parts ?? 0) > 0
                      ? "Continuar video" : "Dividir video completo"
                      : mode === "auto" ? "Recortar clips" : "Recortar tramos"}
                  </Button>
                  <div className="mt-2.5 flex items-center justify-between text-[11px] text-muted-foreground font-mono">
                    <span>1080×1920 · 30fps</span>
                    <span>· borradores</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Footer signal strip */}
            <div
              className="flex items-center gap-3.5 px-5 py-2.5 font-mono text-[11px] text-muted-foreground"
              style={{
                borderTop: "1px solid hsl(var(--border) / .07)",
                background: "hsl(var(--background))",
              }}
            >
              <span style={{ color: "hsl(var(--success))" }}>● local</span>
              <span>·</span>
              <span>ffmpeg + yt-dlp</span>
              <span>·</span>
              <span>subs: {wordsFrom === "captions" ? "vídeo → whisper" : wordsFrom}</span>
              <span className="ml-auto">nada se publica solo</span>
            </div>
          </div>

          {/* SIDE — what it learned + tips */}
          <div className="flex flex-col gap-3.5">
            <TrendsCard trends={trends} />
            <ClipTips />
          </div>
        </div>

        {/* CATALOGUE */}
        <section ref={catalogueRef} className="mt-6 scroll-mt-6">
          <div className="flex flex-wrap items-end justify-between gap-3 mb-3.5">
            <div>
              <span className="eyebrow block mb-1">Catálogo</span>
              <h2 className="font-display text-[20px] font-semibold">Clips para revisar</h2>
            </div>
            <Segmented
              value={filter}
              onChange={(v) => setFilter(v as "" | ClipStatus)}
              accentVar="var(--primary)"
              options={[
                { value: "", label: `Todos · ${clips.length}` },
                { value: "draft", label: "Borradores" },
                { value: "approved", label: "Aprobados" },
                { value: "rejected", label: "Rechazados" },
                { value: "published", label: "Publicados" },
              ]}
            />
          </div>

          {pendingUploads.length > 0 && (
            <div className="soft-panel mb-3.5 flex flex-wrap items-center justify-between gap-3 px-4 py-3">
              <p role="status" className="text-xs text-muted-foreground flex-1">
                {pendingUploads.length === 1 ? "Hay una subida pendiente de confirmar." : `Hay ${pendingUploads.length} subidas pendientes de confirmar.`}
                {" "}Revisa su estado en Studio y resuélvelo antes de iniciar otra subida.
              </p>
              <Button variant="outline" size="sm" disabled={resolutionDisabled} onClick={() => openResolution(pendingUploads[0])}>
                Resolver estado
              </Button>
            </div>
          )}

          {newClips.length > 0 && (
            <div className="soft-panel mb-3.5 flex flex-wrap items-center justify-between gap-3 px-4 py-3">
              <div role="status" className="flex items-center gap-2 text-sm">
                <Check className="h-4 w-4 text-success shrink-0" />
                <span>
                  {newClips.length === 1
                    ? "1 clip nuevo listo para ver"
                    : `${newClips.length} clips nuevos listos para ver`}
                </span>
              </div>
              <Button variant="outline" size="sm" className="gap-1.5" onClick={() => {
                setFilter("");
                openPreview(newClips[0].id);
              }}>
                <Play className="h-3.5 w-3.5" /> Ver clips nuevos
              </Button>
            </div>
          )}

          {loadingClips ? (
            <div className="flex items-center gap-2 text-[13px] text-muted-foreground py-10">
              <Loader2 className="h-4 w-4 animate-spin" /> Cargando clips…
            </div>
          ) : visible.length === 0 ? (
            <EmptyState
              icon={Film}
              title={clips.length ? "Nada en este filtro" : "Todavía no hay clips"}
              description={
                clips.length
                  ? "Cambia el filtro para ver el resto."
                  : "Pega un vídeo arriba y pulsa Recortar. Los clips aparecen aquí como borradores."
              }
            />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-3.5">
              {visible.map((clip) => (
                <ClipCard
                  key={clip.id}
                  clip={clip}
                  isNew={newClipIds.includes(clip.id)}
                  onPreview={() => openPreview(clip.id)}
                  onStatus={(s) => setStatus(clip, s)}
                  onDelete={() => setToDelete(clip)}
                  onUpload={() => openUpload(clip)}
                  uploadDisabled={hasActiveUpload}
                  onResolveUpload={() => openResolution(clip)}
                  resolutionDisabled={resolutionDisabled}
                  onContinue={() => continueClip(clip)}
                  canContinue={canContinueClip(clip, sources)}
                />
              ))}
            </div>
          )}
        </section>
      </PageShell>

      <ProgressDialog
        open={progressOpen}
        onOpenChange={setProgressOpen}
        title={mode === "full" ? "Dividiendo video completo" : mode === "auto" ? "Recortando clips" : "Recortando tramos"}
        description={mode === "full"
          ? "Crea partes consecutivas y guarda el avance para continuar después. Los clips quedan como borradores."
          : "Descarga, subtítulos, cortes y render 9:16. Los clips quedan como borradores."}
        sseUrl={sseUrl}
        onDone={() => void finishRun()}
      />

      <ProgressDialog
        open={uploadProgressOpen}
        onOpenChange={(open) => {
          setUploadProgressOpen(open);
          if (!open) void refreshClips(true);
        }}
        title="Subiendo clip a YouTube"
        description="Usa la sesión del perfil de Firefox del canal seleccionado."
        sseUrl={uploadSseUrl}
        initialPhase="upload"
        successMessage="Proceso de subida finalizado"
        onDone={() => void finishUpload()}
      />

      <Dialog open={!!uploadClip} onOpenChange={(open) => {
        if (!open && !startingUpload) setUploadClip(null);
      }}>
        <DialogContent className="max-w-xl max-h-[90dvh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Subir clip a YouTube</DialogTitle>
            <DialogDescription>
              Elige el canal y revisa el título y la descripción. La subida usa su perfil de Firefox.
            </DialogDescription>
          </DialogHeader>
          <fieldset disabled={startingUpload} className="flex flex-col gap-4 min-w-0">
            <Field label="Canal de YouTube">
              <Select value={uploadChannelId} onValueChange={setUploadChannelId} disabled={startingUpload || loadingUploadChannels}>
                <SelectTrigger aria-label="Canal de YouTube">
                  <SelectValue placeholder={loadingUploadChannels ? "Cargando canales…" : "Selecciona un canal"} />
                </SelectTrigger>
                <SelectContent>
                  {uploadChannels.map((channel) => (
                    <SelectItem key={channel.id} value={channel.id}>
                      {channel.nickname}{channel.profile_ready ? "" : " · perfil no disponible"}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {!loadingUploadChannels && !uploadChannels.length && (
              <p className="text-xs text-muted-foreground">
                Añade primero un canal de YouTube y su perfil de Firefox, como para los Shorts y videos largos.
              </p>
            )}
            {uploadChannel && (
              <div className="rounded-lg border border-border/15 bg-background/60 p-3 text-xs">
                <span className={uploadChannel.profile_ready ? "text-success" : "text-destructive"}>
                  {uploadChannel.profile_ready ? "Perfil de Firefox listo" : "Perfil de Firefox no disponible"}
                </span>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground break-all">
                  {uploadChannel.firefox_profile || "Configura un perfil en el canal o en Ajustes."}
                </p>
                {!uploadChannel.profile_ready && (
                  <p className="mt-2 text-muted-foreground">Revisa la ruta del perfil antes de subir el clip.</p>
                )}
              </div>
            )}
            <Field label="Título" hint={`${uploadTitle.length}/100`}>
              <TextInput value={uploadTitle} onChange={(value) => setUploadTitle(value.slice(0, 100))} />
            </Field>
            <Field label="Descripción" hint={`${uploadDescription.length}/5000`}>
              <TextArea value={uploadDescription} onChange={(value) => setUploadDescription(value.slice(0, 5000))} rows={6} />
            </Field>
          </fieldset>
          {uploadTarget && uploadBlocked(uploadTarget) && (
            <p className="text-xs text-muted-foreground">Este clip ya no está disponible para iniciar otra subida.</p>
          )}
          <div className="flex flex-wrap justify-end gap-2 pt-1">
            <Button variant="outline" disabled={startingUpload} onClick={() => setUploadClip(null)}>Cancelar</Button>
            <Button
              variant="brand"
              onClick={() => void startUpload()}
              disabled={loadingUploadChannels || hasActiveUpload || !uploadChannel?.profile_ready
                || !uploadTitle.trim() || !uploadDescription.trim() || !uploadTarget || uploadBlocked(uploadTarget)}
            >
              {startingUpload ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              {startingUpload ? "Iniciando subida…" : "Subir a YouTube"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={!!resolutionClip} onOpenChange={(open) => {
        if (!open && !resolvingUpload) setResolutionClip(null);
      }}>
        <DialogContent className="max-w-xl max-h-[90dvh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Resolver estado de la subida</DialogTitle>
            <DialogDescription>
              Revisa primero el clip en YouTube Studio. El proceso no pudo confirmar si se publicó.
            </DialogDescription>
          </DialogHeader>
          <p className="text-sm font-medium">{resolutionTarget?.title}</p>
          <Button asChild variant="outline" className="w-fit gap-1.5">
            <a href="https://studio.youtube.com" target="_blank" rel="noopener noreferrer">
              <ExternalLink className="h-4 w-4" /> Revisar YouTube Studio
            </a>
          </Button>
          <fieldset disabled={resolutionDisabled} className="flex flex-col gap-3 min-w-0">
            <Field label="Enlace del video publicado">
              <TextInput value={resolutionUrl} onChange={setResolutionUrl} placeholder="https://www.youtube.com/shorts/…" />
            </Field>
            <p className="text-xs text-muted-foreground">
              Si ya se publicó, pega el enlace del video y confirma para registrarlo en el catálogo.
            </p>
            <Button
              variant="brand"
              disabled={resolutionDisabled || !confirmedVideoUrl || resolutionTarget?.youtube_upload?.status !== "pending_confirmation"}
              onClick={() => void resolveUpload("published")}
            >
              {resolvingUpload ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              Confirmar publicación
            </Button>
            <Divider />
            <p className="text-xs text-muted-foreground">
              Permite un reintento solo si comprobaste en Studio que el clip no se publicó ni sigue subiendo.
              Esta acción libera el estado; no inicia otra subida.
            </p>
            <Button
              variant="outline"
              disabled={resolutionDisabled || resolutionTarget?.youtube_upload?.status !== "pending_confirmation"}
              onClick={() => void resolveUpload("retry")}
            >
              Permitir reintento
            </Button>
          </fieldset>
          {hasRunningUpload && <p className="text-xs text-muted-foreground">Espera a que termine la subida activa antes de resolver este estado.</p>}
          <div className="flex justify-end">
            <Button variant="ghost" disabled={resolvingUpload} onClick={() => setResolutionClip(null)}>Cerrar</Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={!!previewClip} onOpenChange={(open) => !open && setPreviewId(null)}>
        <DialogContent
          className="max-w-2xl max-h-[95dvh] overflow-y-auto"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            previewTrigger.current?.focus({ preventScroll: true });
          }}
        >
          <DialogHeader className="pr-8">
            <DialogTitle>{previewClip?.title || "Vista previa del clip"}</DialogTitle>
            <DialogDescription>
              {previewClip?.source_title || "Fuente local"} · {formatClock(previewClip?.duration)}
            </DialogDescription>
          </DialogHeader>
          {previewClip && (
            <>
              {previewClip.available ? (
                <video
                  key={previewClip.id}
                  src={clipsApi.rawUrl(previewClip.id)}
                  aria-label={`Vista previa: ${previewClip.title || "Clip sin título"}`}
                  controls
                  playsInline
                  preload="metadata"
                  className="w-full max-h-[65dvh] rounded-lg bg-black"
                />
              ) : (
                <p className="text-sm text-muted-foreground">Archivo no encontrado</p>
              )}
              {newClips.length > 1 && newClipIds.includes(previewClip.id) && (
                <div className="flex flex-wrap gap-2" aria-label="Clips nuevos">
                  {newClips.map((clip, index) => (
                    <Button
                      key={clip.id}
                      variant={clip.id === previewClip.id ? "default" : "outline"}
                      size="sm"
                      aria-pressed={clip.id === previewClip.id}
                      onClick={() => setPreviewId(clip.id)}
                    >
                      Clip {index + 1}
                    </Button>
                  ))}
                </div>
              )}
              <ClipUploadAction clip={previewClip} disabled={hasActiveUpload} onUpload={() => openUpload(previewClip)}
                onResolve={() => openResolution(previewClip)} resolutionDisabled={resolutionDisabled} />
              {canContinueClip(previewClip, sources) && (
                <Button variant="outline" className="gap-1.5" onClick={() => continueClip(previewClip)}>
                  <ListVideo className="h-4 w-4" /> Continuar este video
                </Button>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title="¿Borrar este clip?"
        description={
          toDelete
            ? `«${toDelete.title}» se elimina del catálogo y se borra su archivo. El vídeo original se conserva. No se puede deshacer.`
            : undefined
        }
        confirmLabel="Borrar"
        variant="destructive"
        onConfirm={confirmDelete}
      />
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Clip card
// ─────────────────────────────────────────────────────────────────────────────
function ClipCard({
  clip,
  isNew,
  onPreview,
  onStatus,
  onDelete,
  onUpload,
  uploadDisabled,
  onResolveUpload,
  resolutionDisabled,
  onContinue,
  canContinue,
}: {
  clip: Clip;
  isNew: boolean;
  onPreview: () => void;
  onStatus: (s: ClipStatus) => void;
  onDelete: () => void;
  onUpload: () => void;
  uploadDisabled: boolean;
  onResolveUpload: () => void;
  resolutionDisabled: boolean;
  onContinue: () => void;
  canContinue: boolean;
}) {
  const meta = STATUS_META[clip.status] ?? STATUS_META.draft;
  const uploadInFlight = ["uploading", "pending_confirmation"].includes(clip.youtube_upload?.status || "");
  const wordsLabel =
    clip.words_from === "none" || !clip.words_from
      ? "sin subs"
      : clip.words_from.startsWith("captions")
      ? "subs del vídeo"
      : "whisper";
  return (
    <article
      className="studio-surface overflow-hidden flex flex-col"
      style={{ background: "hsl(var(--card))" }}
    >
      <div className="relative bg-black">
        {clip.available ? (
          <video
            src={clipsApi.rawUrl(clip.id)}
            controls
            preload="metadata"
            playsInline
            aria-label={clip.title || "Clip sin título"}
            className="block w-full aspect-[9/16] object-contain bg-black"
          />
        ) : (
          <div className="aspect-[9/16] flex items-center justify-center text-[12px] text-muted-foreground">
            Archivo no encontrado
          </div>
        )}
        <span
          className="absolute top-2 left-2 rounded-md px-2 py-0.5 font-mono text-[10px] font-semibold uppercase"
          style={{
            background: "hsl(var(--background) / .85)",
            color: `hsl(${meta.colorVar})`,
            border: `1px solid hsl(${meta.colorVar} / .35)`,
          }}
        >
          {meta.label}
        </span>
        {isNew && (
          <span className="absolute top-2 right-2 rounded-md bg-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground pointer-events-none">
            Nuevo
          </span>
        )}
      </div>

      <div className="px-3.5 pt-3 pb-3.5 flex flex-col gap-2 flex-1">
        <h3 className="font-display text-[14px] font-semibold leading-snug line-clamp-2" title={clip.title}>
          {clip.title || "Sin título"}
        </h3>
        <div className="text-[11.5px] text-muted-foreground truncate" title={clip.source_url || clip.source_title}>
          {clip.source_title || "Fuente local"}
          {clip.source_uploader ? ` · ${clip.source_uploader}` : ""}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Chip>
            {formatClock(clip.start)}–{formatClock(clip.end)} · {Math.round(clip.duration)}s
          </Chip>
          <Chip>{clip.picked_by === "full" ? "video completo" : clip.picked_by === "manual" ? "a mano" : "auto"}</Chip>
          {clip.sequence_index && <Chip>Parte {clip.sequence_index}{clip.sequence_total ? `/${clip.sequence_total}` : ""}</Chip>}
          {clip.layout && <Chip>{clip.layout}</Chip>}
          <Chip>{wordsLabel}</Chip>
        </div>
        {clip.rights_note && clip.rights_note !== "sin nota (webapp)" && (
          <div className="text-[10.5px] text-muted-foreground/80 italic truncate" title={clip.rights_note}>
            {clip.rights_note}
          </div>
        )}

        <Button
          variant="outline"
          size="sm"
          className="mt-auto gap-1.5"
          disabled={!clip.available}
          onClick={onPreview}
        >
          <Play className="h-3.5 w-3.5" /> Ver clip
        </Button>
        <ClipUploadAction clip={clip} disabled={uploadDisabled} onUpload={onUpload}
          onResolve={onResolveUpload} resolutionDisabled={resolutionDisabled} />
        {canContinue && (
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={onContinue}>
            <ListVideo className="h-3.5 w-3.5" /> Continuar este video
          </Button>
        )}
        <div className="pt-1.5 grid grid-cols-[1fr_1fr_auto] gap-1.5">
          <ReviewButton
            active={clip.status === "approved"}
            colorVar="var(--success)"
            onClick={() => onStatus(clip.status === "approved" ? "draft" : "approved")}
            icon={<Check className="h-3.5 w-3.5" strokeWidth={2} />}
            label="Aprobar"
            disabled={uploadInFlight || clip.status === "published"}
          />
          <ReviewButton
            active={clip.status === "rejected"}
            colorVar="var(--destructive)"
            onClick={() => onStatus(clip.status === "rejected" ? "draft" : "rejected")}
            icon={<X className="h-3.5 w-3.5" strokeWidth={2} />}
            label="Rechazar"
            disabled={uploadInFlight || clip.status === "published"}
          />
          <button
            type="button"
            onClick={onDelete}
            disabled={uploadInFlight}
            className="h-8 w-8 inline-flex items-center justify-center rounded-lg text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ border: "1px solid hsl(var(--border) / .1)", background: "hsl(var(--background))" }}
            title="Borrar clip"
            aria-label="Borrar clip"
          >
            <Trash2 className="h-3.5 w-3.5" strokeWidth={1.7} />
          </button>
        </div>
      </div>
    </article>
  );
}

function ClipUploadAction({ clip, disabled, onUpload, onResolve, resolutionDisabled }: {
  clip: Clip; disabled: boolean; onUpload: () => void; onResolve: () => void; resolutionDisabled: boolean;
}) {
  const upload = clip.youtube_upload;
  const url = safeYouTubeUrl(upload?.url);
  if (upload?.status === "pending_confirmation") {
    return (
      <div className="flex flex-col gap-1.5">
        <Button asChild variant="outline" size="sm" className="w-full gap-1.5">
          <a href="https://studio.youtube.com" target="_blank" rel="noopener noreferrer">
            <ExternalLink className="h-3.5 w-3.5" /> Revisar YouTube Studio
          </a>
        </Button>
        <p className="text-[11px] text-muted-foreground">Subida pendiente de confirmación. Revisa el video en Studio.</p>
        <Button variant="soft" size="sm" className="w-full" disabled={resolutionDisabled} onClick={onResolve}>
          Resolver estado
        </Button>
      </div>
    );
  }
  if (upload?.status === "uploaded" || clip.status === "published") {
    return url ? (
      <Button asChild variant="soft" size="sm" className="w-full gap-1.5">
        <a href={url} target="_blank" rel="noopener noreferrer">
          <ExternalLink className="h-3.5 w-3.5" /> Ver en YouTube
        </a>
      </Button>
    ) : <Button variant="soft" size="sm" disabled>Publicado</Button>;
  }
  const uploading = upload?.status === "uploading";
  return (
    <div className="flex flex-col gap-1.5">
      <Button variant="outline" size="sm" className="w-full gap-1.5" disabled={disabled || uploadBlocked(clip)} onClick={onUpload}>
        {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
        {uploading ? "Subiendo a YouTube…" : upload?.status === "failed" ? "Reintentar subida" : "Subir a YouTube"}
      </Button>
      {upload?.status === "failed" && upload.error && (
        <p role="status" className="text-[11px] text-destructive line-clamp-3 break-words" title={upload.error}>{upload.error}</p>
      )}
      {upload?.channel_nickname && (
        <span className="text-[10.5px] text-muted-foreground">Canal: {upload.channel_nickname}</span>
      )}
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="font-mono text-[10px] px-1.5 py-0.5 rounded-md text-muted-foreground"
      style={{ background: "hsl(var(--background))", border: "1px solid hsl(var(--border) / .07)" }}
    >
      {children}
    </span>
  );
}

function ReviewButton({
  active,
  colorVar,
  onClick,
  icon,
  label,
  disabled = false,
}: {
  active: boolean;
  colorVar: string;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={cn(
        "h-8 rounded-lg px-2 text-[11.5px] font-medium inline-flex items-center justify-center gap-1.5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
        active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
      style={{
        background: active ? `hsl(${colorVar} / .16)` : "hsl(var(--background))",
        border: `1px solid ${active ? `hsl(${colorVar} / .45)` : "hsl(var(--border) / .1)"}`,
      }}
    >
      {icon}
      {label}
    </button>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Side cards
// ─────────────────────────────────────────────────────────────────────────────
function TrendsCard({ trends }: { trends: TrendsResponse | null }) {
  const profile = trends?.profile;
  const traits = Object.entries(profile?.title_traits ?? {})
    .filter(([, s]) => s.meaningful)
    .sort((a, b) => Math.abs(b[1].lift - 1) - Math.abs(a[1].lift - 1));

  return (
    <div
      className="studio-surface relative overflow-hidden px-[18px] py-[18px]"
      style={{ background: "hsl(var(--card))" }}
    >
      <span aria-hidden className="absolute top-0 left-0 right-0 h-[3px]" style={{ background: "hsl(var(--primary))" }} />
      <span className="eyebrow block mb-2.5" style={{ color: "hsl(var(--primary))" }}>
        Lo que aprendió
      </span>
      {!profile ? (
        <div className="text-[13px] text-muted-foreground">Cargando el perfil de tendencias…</div>
      ) : !profile.ready ? (
        <div className="text-[12.5px] text-muted-foreground leading-relaxed">
          Aún no hay historial suficiente para saber qué gana. Importa canales de referencia con{" "}
          <code className="font-mono text-[11px]">scripts/brain.py reference --add @canal</code>.
        </div>
      ) : (
        <>
          <h3 className="font-display text-[18px] font-semibold mb-1">
            {profile.exemplars} ganadores vs {profile.baseline} del resto
          </h3>
          <div className="text-[12px] text-muted-foreground mb-3.5">
            {profile.sample_size} shorts de {profile.channels?.length ?? 0} canales, vistas medidas contra la
            mediana de cada canal
          </div>
          <div className="grid grid-cols-2 gap-2 mb-3.5">
            <Mini
              label="Duración"
              value={`${profile.duration_seconds?.median ?? "—"}s`}
              accentVar="var(--primary)"
            />
            <Mini label="Título" value={`${profile.title_words?.median ?? "—"} pal.`} accentVar="var(--accent)" />
          </div>
          <div className="flex flex-col gap-2 text-[11.5px]">
            <Row k="Ventana" v={`${trends!.window[0]}–${trends!.window[1]}s`} />
            {traits.length === 0 && <Row k="Títulos" v="nada los separa aún" />}
          </div>
          {traits.length > 0 && (
            <ul className="mt-3 flex flex-col gap-2">
              {traits.map(([name, s]) => {
                const good = s.lift >= 1;
                return (
                  <li key={name} className="text-[12px] leading-snug">
                    <span
                      className="font-mono text-[10px] font-semibold mr-1.5"
                      style={{ color: `hsl(var(${good ? "--success" : "--destructive"}))` }}
                    >
                      {good ? "SÍ" : "EVITA"}
                    </span>
                    <span className="text-foreground">{TRAIT_LABELS[name] ?? name}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      — {Math.round(s.winner_share * 100)}% de ganadores vs {Math.round(s.baseline_share * 100)}% del
                      resto
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function ClipTips() {
  return (
    <div className="studio-surface px-[18px] py-[18px]" style={{ background: "hsl(var(--card))" }}>
      <span className="eyebrow text-muted-foreground block mb-2.5">Tips</span>
      <ul className="m-0 pl-4 flex flex-col gap-2 text-[12px] text-muted-foreground leading-relaxed">
        <li>
          <strong className="text-foreground font-medium">Automático</strong> corta por frases habladas: en vídeos
          sin voz usa <strong className="text-foreground font-medium">Tramos a mano</strong>.
        </li>
        <li>
          Los <strong className="text-foreground font-medium">subtítulos del vídeo</strong> aciertan donde Whisper
          falla (gritos, nombres propios).
        </li>
        <li>
          Si el recorte corta la cara, la muestra arriba. Con <strong className="text-foreground font-medium">Completo</strong>,
          conserva el cuadro original y no duplica el rostro. Una caja manual en Facecam fuerza el panel superior.
        </li>
      </ul>
    </div>
  );
}
