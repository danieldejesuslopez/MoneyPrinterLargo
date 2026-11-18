import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { useSearchParams } from "react-router-dom";
import {
  Check,
  Download,
  ExternalLink,
  Gamepad2,
  Loader2,
  Plus,
  RefreshCw,
  Scissors,
  Send,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/layout/Header";
import { PageShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ProgressDialog } from "@/components/ProgressDialog";
import { clipsApi, type ClipUploadChannel } from "@/lib/clips";
import {
  apiRoot,
  gtaRequest,
  gtaUrl,
  NETWORKS,
  STATUS,
  type GtaFact,
  type GtaPost,
  type GtaSnapshot,
  type GtaSource,
} from "@/lib/gta6";
import "./gta6.css";
import { Gta6News } from "./Gta6News";
import { Gta6Autopilot } from "./Gta6Autopilot";

type Values = Record<string, string>;
const TABS = [
  ["today", "Hoy"],
  ["studio", "Estudio"],
  ["autopilot", "Automático"],
  ["sources", "Fuentes y hechos"],
  ["calendar", "Calendario"],
  ["learning", "Aprendizaje"],
  ["setup", "Configuración"],
];
const val = (data: Values, name: string) => data[name] || "";
const yes = (data: Values, name: string) => data[name] === "on";
const ids = (text: string) => (text ? text.split(",").filter(Boolean) : []);

function Form({
  children,
  onSave,
  className = "",
}: {
  children: ReactNode;
  onSave: (data: Values) => void;
  className?: string;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fd = new FormData(event.currentTarget);
    const data: Values = {};
    for (const key of fd.keys())
      data[key] = fd.getAll(key).map(String).join(",");
    onSave(data);
  }
  return (
    <form onSubmit={submit} className={`gta-form ${className}`}>
      {children}
    </form>
  );
}
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="gta-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Box({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="gta-box">
      <div className="gta-box-heading">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}
function Empty({ children }: { children: ReactNode }) {
  return <p className="gta-empty">{children}</p>;
}
function Toggle({
  name,
  children,
  checked = false,
}: {
  name: string;
  children: ReactNode;
  checked?: boolean;
}) {
  return (
    <label className="gta-check">
      <input name={name} type="checkbox" defaultChecked={checked} />
      <span>{children}</span>
    </label>
  );
}
function FactChoices({
  state,
  selected = [],
}: {
  state: GtaSnapshot;
  selected?: string[];
}) {
  return (
    <fieldset className="gta-facts">
      <legend>Hechos que respaldan el texto</legend>
      {state.facts.length ? (
        state.facts.map((f) => (
          <label key={f.id} className="gta-check">
            <input
              type="checkbox"
              name="fact_ids"
              value={f.id}
              defaultChecked={selected.includes(f.id)}
            />
            <span>
              {f.text} <small>{f.fresh ? "Verificado" : "Revisar"}</small>
            </span>
          </label>
        ))
      ) : (
        <small>
          Añade primero hechos con su enlace oficial en Fuentes y hechos.
        </small>
      )}
    </fieldset>
  );
}
function SourceSelect({
  state,
  selected = "",
  required = false,
}: {
  state: GtaSnapshot;
  selected?: string;
  required?: boolean;
}) {
  return (
    <select name="source_id" defaultValue={selected} required={required}>
      <option value="">Selecciona una fuente original</option>
      {state.sources.map((s) => (
        <option key={s.id} value={s.id}>
          {s.name}
          {s.permission === "review" ? " · permiso por revisar" : ""}
          {!s.active ? " · bloqueada" : ""}
        </option>
      ))}
    </select>
  );
}

export function Gta6() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "today";
  const studioTab = params.get("view") || "posts";
  const [state, setState] = useState<GtaSnapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [postEditor, setPostEditor] = useState<Partial<GtaPost> | null>(null);
  const [sourceEditor, setSourceEditor] = useState<Partial<GtaSource> | null>(
    null,
  );
  const [factEditor, setFactEditor] = useState<Partial<GtaFact> | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [filter, setFilter] = useState("all");
  const [calendarDay, setCalendarDay] = useState("");
  const [job, setJob] = useState<{
    url: string;
    title: string;
    upload?: boolean;
  } | null>(null);
  const [channels, setChannels] = useState<ClipUploadChannel[]>([]);
  const [bufferChannels, setBufferChannels] = useState<
    { id: string; name: string; service: string }[]
  >([]);
  const [report, setReport] = useState("");
  const refresh = useCallback(async () => {
    try {
      const next = await gtaRequest<GtaSnapshot>();
      setState(next);
      setError("");
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }, []);
  useEffect(() => {
    void refresh();
    clipsApi
      .uploadChannels()
      .then(setChannels)
      .catch(() => {});
  }, [refresh]);
  async function act(work: () => Promise<unknown>, success = "Guardado") {
    if (busy) return;
    setBusy(true);
    try {
      await work();
      await refresh();
      if (success) toast.success(success);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e), {
        duration: 8000,
      });
    } finally {
      setBusy(false);
    }
  }
  function navigate(next: string, view = "posts") {
    setParams({ tab: next, ...(next === "studio" ? { view } : {}) });
  }
  const selected = state?.posts.find((p) => p.id === selectedId);
  function formatTime(value?: string) {
    return value
      ? new Intl.DateTimeFormat("es", {
          timeZone: state?.settings.timezone,
          dateStyle: "medium",
          timeStyle: "short",
        }).format(new Date(value))
      : "Sin horario";
  }
  function localDay(value: string) {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: state?.settings.timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(value));
  }
  function postCard(post: GtaPost) {
    return (
      <article className="gta-post" key={post.id}>
        <div className="gta-post-meta">
          <span>{NETWORKS[post.platform]}</span>
          <span className={`gta-status gta-status-${post.status}`}>
            {STATUS[post.status] || post.status}
          </span>
        </div>
        <button
          className="gta-post-title"
          onClick={() => setSelectedId(post.id)}
        >
          {post.title}
        </button>
        <p className="gta-post-text">{post.text}</p>
        <div className="gta-post-meta">
          <span>{state?.meta.pillars[post.pillar]}</span>
          <span>
            {post.format === "video"
              ? `${post.length_sec.toFixed(0)} s`
              : "Texto"}
          </span>
        </div>
        {post.scheduled_at && <small>{formatTime(post.scheduled_at)}</small>}
        {post.issues.length > 0 && post.status === "draft" && (
          <small className="gta-warning">
            {post.issues.length} puntos por revisar
          </small>
        )}
        <div className="gta-actions">
          <Button
            size="sm"
            variant="outline"
            onClick={() => setSelectedId(post.id)}
          >
            Revisar
          </Button>
          {["draft", "approved", "planned"].includes(post.status) && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setPostEditor(post)}
            >
              Editar
            </Button>
          )}
          {post.status === "draft" && (
            <Button
              size="sm"
              disabled={busy}
              onClick={() =>
                void act(
                  () => gtaRequest(`/posts/${post.id}/approve`, {}),
                  "Post aprobado",
                )
              }
            >
              Aprobar
            </Button>
          )}
        </div>
      </article>
    );
  }

  return (
    <>
      <Header
        title="GTA 6"
        description="Tu mesa de producción y publicación"
        actions={
          <Button size="sm" variant="outline" asChild>
            <a href={gtaUrl("/export")}>
              <Download />
              Exportar
            </a>
          </Button>
        }
      />
      <PageShell>
        <div className="gta-workspace" aria-busy={busy}>
          <nav className="gta-tabs" aria-label="Secciones de GTA 6">
            {TABS.map(([key, label]) => (
              <button
                key={key}
                className={tab === key ? "active" : ""}
                onClick={() => navigate(key)}
                aria-current={tab === key ? "page" : undefined}
              >
                {label}
              </button>
            ))}
            <button
              className="gta-refresh"
              onClick={() => void refresh()}
              aria-label="Actualizar módulo"
            >
              <RefreshCw size={16} />
            </button>
          </nav>
          {error && (
            <div className="gta-alert" role="alert">
              No se pudo cargar GTA 6: {error}
              <Button
                onClick={() => void refresh()}
                variant="outline"
                size="sm"
              >
                Reintentar
              </Button>
            </div>
          )}
          {!state && !error && (
            <div className="gta-loading">
              <Loader2 className="animate-spin" /> Cargando GTA 6…
            </div>
          )}
          {state && (
            <>
              {tab === "today" && (
                <>
                  <section className="gta-hero">
                    <div className="gta-hero-copy">
                      <span className="gta-fan">Fan page independiente</span>
                      <h1>
                        De la fuente
                        <br />
                        al feed.
                      </h1>
                      <p>
                        Un buen momento. Un corte preciso.
                        <br />
                        Una historia que merece compartirse.
                      </p>
                      <div className="gta-actions">
                        <Button onClick={() => navigate("studio", "news")}>
                          <Sparkles />
                          Generar lote del día
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => navigate("studio", "clips")}
                        >
                          <Scissors />
                          Crear clips
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => {
                            setPostEditor({
                              platform: "x",
                              pillar: "questions",
                              format: "text",
                            });
                          }}
                        >
                          Nuevo borrador
                        </Button>
                      </div>
                    </div>
                    <div className="gta-poster" aria-hidden="true">
                      <div className="gta-sun" />
                      <div className="gta-horizon" />
                      <div className="gta-tower one" />
                      <div className="gta-tower two" />
                      <div className="gta-tower three" />
                      <span>
                        GTA<span>6</span>
                      </span>
                      <small>Tu próximo gran momento</small>
                    </div>
                  </section>
                  <div className="gta-health">
                    <span>
                      <strong>
                        {state.posts.filter((p) => p.status === "draft").length}
                      </strong>{" "}
                      borradores por revisar
                    </span>
                    <span>
                      <strong>
                        {
                          state.posts.filter((p) => p.status === "planned")
                            .length
                        }
                      </strong>{" "}
                      horarios reservados
                    </span>
                    <span>
                      <strong>
                        {state.facts.filter((f) => f.fresh).length}/
                        {state.facts.length}
                      </strong>{" "}
                      hechos vigentes
                    </span>
                    <span>
                      {state.countdown.days === null
                        ? "Cuenta atrás sin configurar"
                        : state.countdown.days >= 0
                          ? `Faltan ${state.countdown.days} días`
                          : "Etapa de lanzamiento"}
                    </span>
                  </div>
                  <div className="gta-columns">
                    <Box title={`Rutina del día · ${state.today}`}>
                      <p className="gta-muted">
                        Los horarios usan {state.settings.timezone}. La reserva
                        local se envía a Buffer cuando tú lo indicas.
                      </p>
                      <div className="gta-routine">
                        {state.meta.routines.map(([id, when, text]) => (
                          <label className="gta-check" key={id}>
                            <input
                              type="checkbox"
                              checked={!!state.checks[`${state.today}:${id}`]}
                              disabled={busy}
                              onChange={(e) =>
                                void act(
                                  () =>
                                    gtaRequest(
                                      "/checks",
                                      {
                                        key: `${state.today}:${id}`,
                                        done: e.target.checked,
                                      },
                                      "PUT",
                                    ),
                                  "",
                                )
                              }
                            />
                            <span>
                              <small>{when}</small>
                              {text}
                            </span>
                          </label>
                        ))}
                      </div>
                    </Box>
                    <Box
                      title="Lo siguiente"
                      action={
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => navigate("calendar")}
                        >
                          Ver calendario
                        </Button>
                      }
                    >
                      {state.posts.filter((p) =>
                        [
                          "planned",
                          "scheduled",
                          "pending_confirmation",
                          "dispatching",
                          "blocked",
                        ].includes(p.status),
                      ).length ? (
                        <div className="gta-upcoming">
                          {[...state.posts]
                            .filter((p) =>
                              [
                                "planned",
                                "scheduled",
                                "pending_confirmation",
                                "dispatching",
                                "blocked",
                              ].includes(p.status),
                            )
                            .sort((a, b) =>
                              (a.scheduled_at || "").localeCompare(
                                b.scheduled_at || "",
                              ),
                            )
                            .slice(0, 5)
                            .map((p) => (
                              <button
                                key={p.id}
                                onClick={() => setSelectedId(p.id)}
                              >
                                <small>{formatTime(p.scheduled_at)}</small>
                                <strong>{p.title}</strong>
                                <span>
                                  {NETWORKS[p.platform]} · {STATUS[p.status]}
                                </span>
                              </button>
                            ))}
                        </div>
                      ) : (
                        <Empty>
                          Prepara tu primer borrador en Estudio, revísalo y
                          reserva su horario.
                        </Empty>
                      )}
                      {state.review.due.length > 0 && (
                        <p className="gta-warning">
                          Hay {state.review.due.length} lecturas de métricas
                          pendientes.
                        </p>
                      )}
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() =>
                          void act(async () => {
                            const response = await fetch(gtaUrl("/report"));
                            if (!response.ok)
                              throw new Error("No se pudo obtener el informe");
                            setReport(await response.text());
                          }, "")
                        }
                      >
                        Leer informe diario
                      </Button>
                    </Box>
                  </div>
                  <Box title="Primeros siete días">
                    <div className="gta-kickoff">
                      {state.meta.kickoff.map((task, i) => (
                        <label className="gta-check" key={i}>
                          <input
                            type="checkbox"
                            disabled={busy}
                            checked={!!state.checks[`kickoff:${i}`]}
                            onChange={(e) =>
                              void act(
                                () =>
                                  gtaRequest(
                                    "/checks",
                                    {
                                      key: `kickoff:${i}`,
                                      done: e.target.checked,
                                    },
                                    "PUT",
                                  ),
                                "",
                              )
                            }
                          />
                          <span>
                            <small>Día {i + 1}</small>
                            {task}
                          </span>
                        </label>
                      ))}
                    </div>
                  </Box>
                  {state.settings.launch_mode && (
                    <Box title="Mesa de lanzamiento">
                      <ol className="gta-playbook">
                        <li>
                          Verifica la disponibilidad en una fuente oficial y
                          limpia la cola del estreno.
                        </li>
                        <li>
                          Abre los videos de creadores con permiso. Prioriza
                          momentos que se entiendan solos.
                        </li>
                        <li>
                          Recorta 7–30 segundos, añade gancho, comentario y
                          crédito. Marca spoilers narrativos.
                        </li>
                        <li>
                          Revisa y publica en X, Reels, TikTok y Shorts.
                          Objetivo: 15–30 minutos desde el momento.
                        </li>
                        <li>
                          Revisa cada 1–2 horas durante las primeras 48 h.
                          Convierte preguntas repetidas en guías.
                        </li>
                      </ol>
                    </Box>
                  )}
                </>
              )}

              {tab === "studio" && (
                <>
                  <div className="gta-section-heading">
                    <div>
                      <h1>Estudio</h1>
                      <p>
                        Del momento original a un borrador listo para revisar.
                      </p>
                    </div>
                    <Button
                      onClick={() =>
                        setPostEditor({
                          platform: "x",
                          pillar: "questions",
                          format: "text",
                        })
                      }
                    >
                      <Plus />
                      Nuevo borrador
                    </Button>
                  </div>
                  <div className="gta-subtabs">
                    {[
                      ["posts", "Borradores"],
                      ["ideas", "Banco de ideas"],
                      ["clips", "Producir clips"],
                      ["news", "Noticias narradas"],
                    ].map(([key, label]) => (
                      <button
                        className={studioTab === key ? "active" : ""}
                        key={key}
                        onClick={() => navigate("studio", key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  {studioTab === "news" && (
                    <Gta6News state={state} onChange={refresh} onJob={setJob} />
                  )}
                  {studioTab === "posts" && (
                    <>
                      <div className="gta-filter">
                        <label>
                          Estado{" "}
                          <select
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                          >
                            <option value="all">Todos</option>
                            {Object.entries(STATUS).map(([k, v]) => (
                              <option key={k} value={k}>
                                {v}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <div className="gta-post-grid">
                        {state.posts
                          .filter(
                            (p) => filter === "all" || p.status === filter,
                          )
                          .map(postCard)}
                      </div>
                      {!state.posts.length && (
                        <Empty>
                          Crea un borrador desde una idea o produce clips a
                          partir de una fuente autorizada.
                        </Empty>
                      )}
                    </>
                  )}
                  {studioTab === "ideas" && (
                    <>
                      <div className="gta-columns">
                        <Box title="Proponer ideas">
                          <Form
                            onSave={(d) =>
                              void act(async () => {
                                const result = await gtaRequest<{
                                  ideas: unknown[];
                                  warning: string;
                                }>("/ideas/generate", {
                                  count: Number(d.count),
                                  use_llm: yes(d, "use_llm"),
                                });
                                if (result.warning)
                                  toast.info(result.warning, {
                                    duration: 9000,
                                  });
                              }, "Ideas preparadas")
                            }
                          >
                            <Field label="Cantidad">
                              <input
                                name="count"
                                type="number"
                                min="1"
                                max="30"
                                defaultValue="15"
                                required
                              />
                            </Field>
                            <Toggle name="use_llm" checked>
                              Usar el modelo de texto configurado en
                              MoneyPrinter
                            </Toggle>
                            <Button disabled={busy}>
                              <Sparkles />
                              Proponer ideas
                            </Button>
                          </Form>
                          <p className="gta-muted">
                            Usa los hechos vigentes y las lecciones guardadas.
                            Cada propuesta sigue siendo un borrador.
                          </p>
                        </Box>
                        <Box title="Guardar una idea">
                          <Form
                            onSave={(d) =>
                              void act(
                                () =>
                                  gtaRequest("/ideas", {
                                    ...d,
                                    fact_ids: ids(d.fact_ids),
                                  }),
                                "Idea guardada",
                              )
                            }
                          >
                            <Field label="Título">
                              <input name="title" required />
                            </Field>
                            <Field label="Pilar">
                              <select name="pillar">
                                {Object.entries(state.meta.pillars).map(
                                  ([k, v]) => (
                                    <option key={k} value={k}>
                                      {v}
                                    </option>
                                  ),
                                )}
                              </select>
                            </Field>
                            <Field label="Ángulo o valor">
                              <textarea name="angle" />
                            </Field>
                            <FactChoices state={state} />
                            <Button disabled={busy}>Guardar idea</Button>
                          </Form>
                        </Box>
                      </div>
                      <div className="gta-idea-list">
                        {state.ideas.map((i) => (
                          <article key={i.id}>
                            <small>
                              {state.meta.pillars[i.pillar]}
                              {i.used ? " · utilizada" : ""}
                            </small>
                            <h3>{i.title}</h3>
                            <p>{i.angle}</p>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() =>
                                setPostEditor({
                                  title: i.title,
                                  text: i.title,
                                  pillar: i.pillar,
                                  fact_ids: i.fact_ids,
                                  platform: "x",
                                  format: "text",
                                  commentary: i.angle,
                                  ...({ idea_id: i.id } as object),
                                })
                              }
                            >
                              Preparar borrador
                            </Button>
                          </article>
                        ))}
                      </div>
                    </>
                  )}
                  {studioTab === "clips" && (
                    <div className="gta-columns">
                      <Box title="Crear clips GTA 6">
                        <p className="gta-muted">
                          El motor Clips de MoneyPrinter se encarga del corte,
                          caras y subtítulos. El perfil GTA 6 propone momentos
                          de 7–30 s y quema el gancho en pantalla.
                        </p>
                        <Form
                          onSave={(d) =>
                            void act(async () => {
                              const result = await gtaRequest<{
                                stream_url: string;
                              }>("/clips", {
                                target: d.target,
                                source_id: d.source_id,
                                mode: d.mode,
                                count: Number(d.count),
                                min_seconds: Number(d.min_seconds),
                                max_seconds: Number(d.max_seconds),
                                layout: d.layout,
                                facecam: d.facecam,
                                language: d.language,
                                commentary: d.commentary,
                                subtitles: yes(d, "subtitles"),
                                use_llm: yes(d, "use_llm"),
                                spoiler: yes(d, "spoiler"),
                                platform: d.platform,
                                spans: (d.spans || "")
                                  .split(/[;\n]+/)
                                  .map((s) => s.trim())
                                  .filter(Boolean),
                                titles: (d.titles || "")
                                  .split("\n")
                                  .map((s) => s.trim()),
                              });
                              setJob({
                                url: apiRoot + result.stream_url,
                                title: "GTA 6 · crear clips",
                              });
                            }, "")
                          }
                        >
                          <Field label="Fuente original">
                            <SourceSelect state={state} required />
                          </Field>
                          <Field label="URL del video o ruta local">
                            <input
                              name="target"
                              required
                              placeholder="https://www.youtube.com/watch?v=… o C:\Videos\gameplay.mp4"
                            />
                          </Field>
                          <div className="gta-fields-row">
                            <Field label="Modo">
                              <select name="mode">
                                <option value="auto">Mejores momentos</option>
                                <option value="cut">Tramos manuales</option>
                                <option value="full">
                                  Video completo / continuar
                                </option>
                              </select>
                            </Field>
                            <Field label="Clips por lote">
                              <input
                                name="count"
                                type="number"
                                min="1"
                                max="20"
                                defaultValue="3"
                              />
                            </Field>
                          </div>
                          <div className="gta-fields-row">
                            <Field label="Mínimo (s)">
                              <input
                                name="min_seconds"
                                type="number"
                                min="5"
                                max="179"
                                defaultValue="7"
                              />
                            </Field>
                            <Field label="Máximo / duración por parte (s)">
                              <input
                                name="max_seconds"
                                type="number"
                                min="6"
                                max="180"
                                defaultValue="30"
                              />
                            </Field>
                          </div>
                          <Field
                            label="Tramos manuales"
                            hint="Solo en modo manual. Uno por línea o separados por ;"
                          >
                            <textarea
                              name="spans"
                              placeholder="1:32-1:58; 4:05-4:28"
                            />
                          </Field>
                          <div className="gta-fields-row">
                            <Field label="Encuadre">
                              <select name="layout">
                                <option value="blur">
                                  Cuadro completo / fondo desenfocado
                                </option>
                                <option value="crop">
                                  Recorte y seguimiento de cara
                                </option>
                                <option value="facecam">
                                  Cámara y gameplay
                                </option>
                              </select>
                            </Field>
                            <Field label="Idioma del audio">
                              <input
                                name="language"
                                placeholder="es, en… o automático"
                              />
                            </Field>
                          </div>
                          <Field
                            label="Cámara manual (opcional)"
                            hint="Para Cámara y gameplay: x,y,ancho,alto en píxeles de la fuente."
                          >
                            <input
                              name="facecam"
                              placeholder="898,11,360,200"
                            />
                          </Field>
                          <Field
                            label="Ganchos de los tramos manuales"
                            hint="Uno por línea, en el mismo orden que los tramos. Se queman en el video al renderizar."
                          >
                            <textarea
                              name="titles"
                              placeholder="El giro que no esperabas"
                            />
                          </Field>
                          <Field label="Comentario o valor propio">
                            <textarea
                              name="commentary"
                              required
                              placeholder="Qué detalle destacarás o por qué vale la pena ver este momento"
                            />
                          </Field>
                          <Field label="Primer borrador para">
                            <select name="platform" defaultValue="youtube">
                              {state.settings.platforms.map((p) => (
                                <option key={p} value={p}>
                                  {NETWORKS[p]}
                                </option>
                              ))}
                            </select>
                          </Field>
                          <Toggle name="subtitles" checked>
                            Subtítulos en pantalla (desactivar si no hay voz)
                          </Toggle>
                          <Toggle name="use_llm" checked>
                            Evaluar gancho, remate y contexto con el modelo
                          </Toggle>
                          <Toggle name="spoiler">
                            Contiene spoiler narrativo
                          </Toggle>
                          <Button disabled={busy}>
                            <Scissors />
                            Crear clips y borradores
                          </Button>
                        </Form>
                      </Box>
                      <div className="gta-stack">
                        <Box title="Antes de producir">
                          <ul className="gta-checklist">
                            <li>
                              Fuente original activa, crédito y base de uso
                              registrados.
                            </li>
                            <li>
                              Gameplay real, completo y sin material filtrado.
                            </li>
                            <li>
                              Gancho desde el primer segundo y remate claro.
                            </li>
                            <li>Revisar cada resultado antes de aprobarlo.</li>
                          </ul>
                          <p className="gta-muted">
                            La puntuación compara candidatos; no predice vistas.
                            El comentario se guarda en el borrador. Si activaste
                            voz en off en Configuración, también se narra y
                            subtitula sobre el gameplay.
                          </p>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => navigate("sources")}
                          >
                            Gestionar fuentes
                          </Button>
                        </Box>
                        <Box title="Producciones recientes">
                          {state.clip_jobs?.length ? (
                            state.clip_jobs
                              .slice(-6)
                              .reverse()
                              .map((j) => (
                                <div className="gta-log" key={j.id}>
                                  <strong>
                                    {j.status === "done"
                                      ? `${j.clip_ids.length} clips preparados`
                                      : j.status}
                                  </strong>
                                  {j.error && (
                                    <p className="gta-warning">{j.error}</p>
                                  )}
                                </div>
                              ))
                          ) : (
                            <Empty>
                              Los resultados aparecerán aquí y en Borradores.
                            </Empty>
                          )}
                        </Box>
                      </div>
                    </div>
                  )}
                </>
              )}

              {tab === "autopilot" && (
                <Gta6Autopilot onChange={refresh} onJob={setJob} />
              )}
              {tab === "sources" && (
                <>
                  <div className="gta-section-heading">
                    <div>
                      <h1>Una fuente detrás de cada post.</h1>
                      <p>
                        Comprueba lo oficial, identifica al creador y registra
                        la base de uso.
                      </p>
                    </div>
                    <Button
                      onClick={() =>
                        setSourceEditor({
                          kind: "creator",
                          permission: "review",
                          active: true,
                        })
                      }
                    >
                      <Plus />
                      Añadir fuente
                    </Button>
                  </div>
                  <div className="gta-columns">
                    <Box title="Fuentes y creadores">
                      <p className="gta-muted">
                        {
                          state.sources.filter((s) => s.kind === "creator")
                            .length
                        }
                        {" "}creadores en tu lista (objetivo: al menos 50). Los enlaces de descubrimiento
                        sirven para encontrar al autor original.
                      </p>
                      <div className="gta-source-list">
                        {state.sources.map((s) => (
                          <article key={s.id}>
                            <div>
                              <a href={s.url} target="_blank" rel="noreferrer">
                                {s.name} <ExternalLink size={12} />
                              </a>
                              <small>
                                {s.kind === "official"
                                  ? "Fuente oficial"
                                  : s.kind === "creator"
                                    ? "Creador"
                                    : "Descubrimiento"}{" "}
                                ·{" "}
                                {s.permission === "review"
                                  ? "Revisar permiso"
                                  : s.permission === "denied"
                                    ? "Permiso retirado"
                                    : "Base de uso registrada"}
                                {!s.active ? " · bloqueada" : ""}
                              </small>
                              {s.credit && <small>Crédito: {s.credit}</small>}
                            </div>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setSourceEditor(s)}
                            >
                              Editar
                            </Button>
                          </article>
                        ))}
                      </div>
                    </Box>
                    <Box
                      title="Hechos verificados"
                      action={
                        <Button
                          size="sm"
                          onClick={() =>
                            setFactEditor({ status: "unverified" })
                          }
                        >
                          <Plus />
                          Añadir hecho
                        </Button>
                      }
                    >
                      <p className="gta-muted">
                        Vigencia de 48 horas. Abre el enlace, comprueba el dato
                        y registra la revisión. El modelo no verifica fuentes
                        por su cuenta.
                      </p>
                      {state.facts.length ? (
                        state.facts.map((f) => (
                          <article key={f.id} className="gta-fact">
                            <span
                              className={`gta-status ${f.fresh ? "gta-status-approved" : "gta-status-blocked"}`}
                            >
                              <ShieldCheck size={12} />
                              {f.fresh
                                ? "Vigente"
                                : f.status === "withdrawn"
                                  ? "Retirado"
                                  : "Por revisar"}
                            </span>
                            <p>{f.text}</p>
                            <a href={f.url} target="_blank" rel="noreferrer">
                              Abrir fuente oficial <ExternalLink size={12} />
                            </a>
                            <small>
                              {f.verified_at
                                ? `Última revisión: ${formatTime(f.verified_at)}`
                                : "Sin revisión registrada"}
                            </small>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => setFactEditor(f)}
                            >
                              Revisar / actualizar
                            </Button>
                          </article>
                        ))
                      ) : (
                        <Empty>
                          Empieza con un dato del sitio de Rockstar. Conserva el
                          enlace exacto que lo respalda.
                        </Empty>
                      )}
                    </Box>
                  </div>
                  <Box title="Retiradas y solicitudes">
                    <Form
                      onSave={(d) =>
                        void act(
                          () =>
                            gtaRequest(`/sources/${d.source_id}/takedown`, {
                              text: d.text,
                            }),
                          "Fuente y posts locales bloqueados; revisa también las colas externas",
                        )
                      }
                    >
                      <Field label="Fuente afectada">
                        <SourceSelect state={state} required />
                      </Field>
                      <Field label="Motivo / solicitud">
                        <textarea name="text" required />
                      </Field>
                      <Button variant="destructive" disabled={busy}>
                        Bloquear fuente y sus posts
                      </Button>
                    </Form>
                    <p className="gta-muted">
                      El bloqueo local conserva el registro. Cancela los envíos
                      en Buffer y retira las publicaciones en cada red.
                    </p>
                    {state.takedowns.map((t) => (
                      <div className="gta-log" key={t.id}>
                        <strong>{t.reason}</strong>
                        <p>{t.action}</p>
                      </div>
                    ))}
                  </Box>
                </>
              )}

              {tab === "calendar" && (
                <>
                  <div className="gta-section-heading">
                    <div>
                      <h1>Tu calendario editorial.</h1>
                      <p>
                        {state.settings.timezone} · Reserva local → envío a
                        Buffer → confirmación de publicación.
                      </p>
                    </div>
                  </div>
                  <Box title="Preparar un día">
                    <Form
                      className="gta-inline-form"
                      onSave={(d) =>
                        void act(
                          () =>
                            gtaRequest("/calendar/plan-day", { day: d.day }),
                          "Horarios locales reservados para los borradores aprobados",
                        )
                      }
                    >
                      <Field label="Fecha">
                        <input
                          name="day"
                          type="date"
                          defaultValue={state.today}
                          required
                        />
                      </Field>
                      <Button disabled={busy}>
                        Reservar horarios de aprobados
                      </Button>
                    </Form>
                    <p className="gta-muted">
                      Usa tus franjas y límites diarios. Envía cada reserva a
                      Buffer desde su ficha, después de revisarla.
                    </p>
                  </Box>
                  <div className="gta-filter">
                    <label>
                      Mostrar fecha{" "}
                      <input
                        type="date"
                        value={calendarDay}
                        onChange={(e) => setCalendarDay(e.target.value)}
                      />
                    </label>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setCalendarDay("")}
                    >
                      Todas
                    </Button>
                  </div>
                  <div className="gta-calendar">
                    {[...state.posts]
                      .filter(
                        (p) =>
                          [
                            "approved",
                            "planned",
                            "scheduled",
                            "published",
                            "pending_confirmation",
                            "dispatching",
                            "blocked",
                          ].includes(p.status) &&
                          (!calendarDay ||
                            ((p.published_at || p.scheduled_at) &&
                              localDay(p.published_at || p.scheduled_at) ===
                                calendarDay)),
                      )
                      .sort((a, b) =>
                        (a.scheduled_at || a.published_at || "").localeCompare(
                          b.scheduled_at || b.published_at || "",
                        ),
                      )
                      .map((p) => (
                        <button key={p.id} onClick={() => setSelectedId(p.id)}>
                          <time>
                            {formatTime(p.published_at || p.scheduled_at)}
                          </time>
                          <strong>{p.title}</strong>
                          <span>{NETWORKS[p.platform]}</span>
                          <span className={`gta-status gta-status-${p.status}`}>
                            {STATUS[p.status]}
                          </span>
                        </button>
                      ))}
                  </div>
                  {!state.posts.some((p) => p.status !== "draft") && (
                    <Empty>
                      Aprueba un borrador del Estudio para colocarlo aquí.
                    </Empty>
                  )}
                </>
              )}

              {tab === "learning" && (
                <>
                  <div className="gta-section-heading">
                    <div>
                      <h1>Aprender de lo que ocurrió.</h1>
                      <p>
                        Lecturas reales, una variable por prueba y decisiones
                        con evidencia.
                      </p>
                    </div>
                  </div>
                  <Box title="Revisión semanal">
                    <p className="gta-muted">{state.review.note}</p>
                    <p>
                      {state.review.sample
                        ? `${state.review.sample} publicaciones con lectura de ${state.review.read_hours} horas.`
                        : "Todavía no hay suficientes lecturas de 24 o 72 horas."}
                    </p>
                    <div className="gta-columns">
                      {[
                        ["Mejores publicaciones", state.review.top],
                        ["Publicaciones por mejorar", state.review.bottom],
                      ].map(([label, rows]) => (
                        <div key={String(label)}>
                          <h3>{String(label)}</h3>
                          {(rows as typeof state.review.top).map((p) => (
                            <button
                              className="gta-ranking"
                              key={p.id}
                              onClick={() => setSelectedId(p.id)}
                            >
                              <span>{p.title}</span>
                              <strong>
                                {p.views.toLocaleString()} <small>vistas</small>
                              </strong>
                            </button>
                          ))}
                        </div>
                      ))}
                    </div>
                    {state.review.medians.length > 0 && (
                      <div className="gta-table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>Grupo</th>
                              <th>Valor</th>
                              <th>Mediana de vistas</th>
                              <th>Muestra</th>
                            </tr>
                          </thead>
                          <tbody>
                            {state.review.medians.map((m) => (
                              <tr key={m.dimension + m.value}>
                                <td>
                                  {
                                    (
                                      {
                                        platform: "Red",
                                        pillar: "Pilar",
                                        slot: "Hora",
                                      } as Record<string, string>
                                    )[m.dimension]
                                  }
                                </td>
                                <td>
                                  {NETWORKS[m.value] ||
                                    state.meta.pillars[m.value] ||
                                    m.value}
                                </td>
                                <td>{m.median.toLocaleString()}</td>
                                <td>{m.sample}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </Box>
                  <div className="gta-columns">
                    <Box title="Registrar métricas">
                      <Form
                        onSave={(d) =>
                          void act(
                            () =>
                              gtaRequest(`/posts/${d.post_id}/metrics`, {
                                hours: Number(d.hours),
                                ...Object.fromEntries(
                                  [
                                    "views",
                                    "likes",
                                    "reposts",
                                    "replies",
                                    "saves",
                                    "bookmarks",
                                    "follows",
                                    "retention",
                                  ].map((k) => [
                                    k,
                                    d[k] === "" ? null : Number(d[k]),
                                  ]),
                                ),
                              }),
                            "Lectura registrada",
                          )
                        }
                      >
                        <Field label="Publicación">
                          <select name="post_id" required>
                            <option value="">
                              Elige una publicación confirmada
                            </option>
                            {state.posts
                              .filter((p) => p.status === "published")
                              .map((p) => (
                                <option key={p.id} value={p.id}>
                                  {p.title} · {NETWORKS[p.platform]}
                                </option>
                              ))}
                          </select>
                        </Field>
                        <Field label="Edad de la lectura">
                          <select name="hours">
                            <option value="12">12 horas</option>
                            <option value="24">24 horas</option>
                            <option value="72">72 horas</option>
                          </select>
                        </Field>
                        <div className="gta-fields-row">
                          {[
                            ["views", "Vistas"],
                            ["likes", "Me gusta"],
                            ["reposts", "Reposts / compartidos"],
                            ["replies", "Respuestas"],
                            ["saves", "Guardados"],
                            ["bookmarks", "Marcadores"],
                            ["follows", "Seguidores ganados"],
                            ["retention", "Retención (%)"],
                          ].map(([key, label]) => (
                            <Field key={key} label={label}>
                              <input
                                name={key}
                                type="number"
                                min="0"
                                step={key === "retention" ? "0.1" : "1"}
                                max={key === "retention" ? 100 : undefined}
                              />
                            </Field>
                          ))}
                        </div>
                        <p className="gta-muted">
                          Deja vacío lo que la red no muestre. Cero significa
                          que observaste un cero.
                        </p>
                        <Button disabled={busy}>Guardar lectura</Button>
                      </Form>
                      {state.review.due.slice(0, 8).map((d) => (
                        <button
                          className="gta-due"
                          key={d.post_id + d.hours}
                          onClick={() => setSelectedId(d.post_id)}
                        >
                          {d.hours} h pendientes: {d.title}
                        </button>
                      ))}
                    </Box>
                    <div className="gta-stack">
                      <Box title="Una lección">
                        <Form
                          onSave={(d) =>
                            void act(
                              () => gtaRequest("/notes/lessons", d),
                              "Lección guardada",
                            )
                          }
                        >
                          <Field label="Tipo">
                            <select name="kind">
                              <option value="win">Funcionó</option>
                              <option value="loss">No funcionó</option>
                              <option value="rule">Regla actual</option>
                            </select>
                          </Field>
                          <Field label="Qué aprendiste">
                            <textarea name="text" required />
                          </Field>
                          <Field label="Evidencia o publicaciones">
                            <input name="evidence" />
                          </Field>
                          <Button disabled={busy}>Guardar lección</Button>
                        </Form>
                        {state.lessons
                          .slice(-5)
                          .reverse()
                          .map((n) => (
                            <div className="gta-log" key={n.id}>
                              <small>
                                {
                                  (
                                    {
                                      win: "Funcionó",
                                      loss: "No funcionó",
                                      rule: "Regla",
                                    } as Record<string, string>
                                  )[n.kind]
                                }
                              </small>
                              <p>{n.text}</p>
                              <small>{n.evidence}</small>
                            </div>
                          ))}
                      </Box>
                      <Box title="Prueba de una variable">
                        <Form
                          onSave={(d) =>
                            void act(
                              () => gtaRequest("/notes/experiments", d),
                              "Prueba registrada",
                            )
                          }
                        >
                          <Field label="Nombre">
                            <input name="name" required />
                          </Field>
                          <Field label="Variable">
                            <select name="variable">
                              <option value="hook">Gancho</option>
                              <option value="length">Duración</option>
                              <option value="time">Horario</option>
                              <option value="caption">Texto del post</option>
                              <option value="source_type">
                                Tipo de fuente
                              </option>
                            </select>
                          </Field>
                          <div className="gta-fields-row">
                            <Field label="Control">
                              <input name="control" required />
                            </Field>
                            <Field label="Variante">
                              <input name="variant" required />
                            </Field>
                          </div>
                          <Button disabled={busy}>Crear prueba de hoy</Button>
                        </Form>
                        {state.experiments
                          .slice(-4)
                          .reverse()
                          .map((x) => (
                            <div className="gta-log" key={x.id}>
                              <strong>{x.name}</strong>
                              <p>
                                {x.control} / {x.variant}
                              </p>
                              {x.status === "running" ? (
                                <Form
                                  onSave={(d) =>
                                    void act(
                                      () =>
                                        gtaRequest(
                                          `/experiments/${x.id}/complete`,
                                          d,
                                        ),
                                      "Resultado guardado",
                                    )
                                  }
                                >
                                  <Field label="Resultado observado">
                                    <input name="text" required />
                                  </Field>
                                  <Button size="sm" disabled={busy}>
                                    Cerrar prueba
                                  </Button>
                                </Form>
                              ) : (
                                <small>{x.result}</small>
                              )}
                            </div>
                          ))}
                      </Box>
                    </div>
                  </div>
                  <div className="gta-columns">
                    <Box title="Preguntas de la comunidad">
                      <Form
                        onSave={(d) =>
                          void act(
                            () => gtaRequest("/notes/engagement", d),
                            "Pregunta guardada",
                          )
                        }
                      >
                        <Field label="Pregunta recurrente">
                          <textarea name="question" required />
                        </Field>
                        <Field label="Enlace al comentario">
                          <input name="url" type="url" />
                        </Field>
                        <Field label="Borrador de respuesta">
                          <textarea name="answer" />
                        </Field>
                        <Button disabled={busy}>
                          Guardar pregunta y respuesta
                        </Button>
                      </Form>
                      {state.engagement
                        .slice(-8)
                        .reverse()
                        .map((q) => (
                          <div className="gta-log" key={q.id}>
                            <strong>{q.question}</strong>
                            <p>{q.answer}</p>
                            {q.url && (
                              <a href={q.url} target="_blank" rel="noreferrer">
                                Abrir conversación
                              </a>
                            )}
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={() =>
                                void act(
                                  () =>
                                    gtaRequest(`/engagement/${q.id}/idea`, {}),
                                  "Pregunta añadida al banco de ideas",
                                )
                              }
                            >
                              Convertir en idea
                            </Button>
                          </div>
                        ))}
                      <p className="gta-muted">
                        Las respuestas son borradores. Publícalas tú después de
                        verificar los datos.
                      </p>
                    </Box>
                    <Box title="Estudiar otras páginas">
                      <Form
                        onSave={(d) =>
                          void act(
                            () => gtaRequest("/notes/references", d),
                            "Observación guardada",
                          )
                        }
                      >
                        <Field label="Página">
                          <input name="name" required />
                        </Field>
                        <Field label="URL">
                          <input name="url" type="url" required />
                        </Field>
                        <Field label="Formatos que funcionan">
                          <input name="formats" />
                        </Field>
                        <Field label="Ganchos / duración">
                          <input name="hooks" />
                        </Field>
                        <Field label="Qué aprender sin copiar">
                          <textarea name="lesson" />
                        </Field>
                        <Button disabled={busy}>Guardar observación</Button>
                      </Form>
                      {state.references.slice(-6).map((r) => (
                        <div className="gta-log" key={r.id}>
                          <a href={r.url} target="_blank" rel="noreferrer">
                            {r.name}
                          </a>
                          <p>
                            {r.formats} · {r.hooks}
                          </p>
                          <small>{r.lesson}</small>
                        </div>
                      ))}
                    </Box>
                  </div>
                </>
              )}

              {tab === "setup" && (
                <>
                  <div className="gta-section-heading">
                    <div>
                      <h1>Tu página, tus reglas.</h1>
                      <p>
                        Configura la identidad y el ritmo de trabajo sin cambiar
                        los otros módulos.
                      </p>
                    </div>
                  </div>
                  <Box title="Identidad y operación">
                    <Form
                      key={state.settings.name + state.settings.timezone}
                      onSave={(d) =>
                        void act(
                          () =>
                            gtaRequest(
                              "/settings",
                              {
                                name: d.name,
                                handle: d.handle,
                                timezone: d.timezone,
                                language: d.language,
                                angle: d.angle,
                                bio: d.bio,
                                voice: d.voice,
                                voice_mode: d.voice_mode,
                                avoid_creators: d.avoid_creators,
                                approval_hours: d.approval_hours,
                                daily_x: Number(d.daily_x),
                                daily_video: Number(d.daily_video),
                                slots: d.slots
                                  .split(",")
                                  .map((s) => s.trim())
                                  .filter(Boolean),
                                platforms: ids(d.platforms),
                                launch_date: d.launch_date,
                                launch_fact_id: d.launch_fact_id,
                                kickoff_start: d.kickoff_start,
                                launch_mode: yes(d, "launch_mode"),
                              },
                              "PATCH",
                            ),
                          "Configuración guardada",
                        )
                      }
                    >
                      <div className="gta-fields-row">
                        <Field label="Nombre">
                          <input
                            name="name"
                            defaultValue={state.settings.name}
                            required
                          />
                        </Field>
                        <Field label="Handle">
                          <input
                            name="handle"
                            defaultValue={state.settings.handle}
                            placeholder="@tu_pagina_gta6"
                          />
                        </Field>
                        <Field label="Zona horaria">
                          <input
                            name="timezone"
                            defaultValue={state.settings.timezone}
                            required
                          />
                        </Field>
                        <Field label="Idioma">
                          <input
                            name="language"
                            defaultValue={state.settings.language}
                          />
                        </Field>
                      </div>
                      <Field label="Ángulo de la página">
                        <input
                          name="angle"
                          defaultValue={state.settings.angle}
                        />
                      </Field>
                      <Field label="Bio">
                        <textarea
                          name="bio"
                          defaultValue={state.settings.bio}
                        />
                      </Field>
                      <fieldset className="gta-facts">
                        <legend>Redes de la página</legend>
                        <div className="gta-actions">
                          {state.meta.platforms.map((p) => (
                            <label className="gta-check" key={p}>
                              <input
                                type="checkbox"
                                name="platforms"
                                value={p}
                                defaultChecked={state.settings.platforms.includes(
                                  p,
                                )}
                              />
                              {NETWORKS[p]}
                            </label>
                          ))}
                        </div>
                      </fieldset>
                      <div className="gta-fields-row">
                        <Field label="Voz faceless">
                          <select
                            name="voice_mode"
                            defaultValue={state.settings.voice_mode}
                          >
                            <option value="text">
                              Texto y sonido original
                            </option>
                            <option value="voiceover">Voz en off</option>
                          </select>
                        </Field>
                        <Field label="Voz de MoneyPrinter / alias">
                          <input
                            name="voice"
                            defaultValue={state.settings.voice}
                            placeholder="Voz configurada en el canal"
                          />
                        </Field>
                        <Field label="Horario para revisar">
                          <input
                            name="approval_hours"
                            defaultValue={state.settings.approval_hours}
                          />
                        </Field>
                        <Field label="Creadores o temas a evitar">
                          <input
                            name="avoid_creators"
                            defaultValue={state.settings.avoid_creators}
                          />
                        </Field>
                        <Field label="Posts diarios en X">
                          <input
                            name="daily_x"
                            type="number"
                            min="1"
                            max="20"
                            defaultValue={state.settings.daily_x}
                          />
                        </Field>
                        <Field label="Videos diarios por red">
                          <input
                            name="daily_video"
                            type="number"
                            min="1"
                            max="10"
                            defaultValue={state.settings.daily_video}
                          />
                        </Field>
                      </div>
                      <Field
                        label="Franjas horarias"
                        hint="Separadas por comas, en la zona horaria de la página."
                      >
                        <input
                          name="slots"
                          defaultValue={state.settings.slots.join(", ")}
                        />
                      </Field>
                      <div className="gta-fields-row">
                        <Field label="Inicio de los siete días">
                          <input
                            name="kickoff_start"
                            type="date"
                            defaultValue={state.settings.kickoff_start}
                          />
                        </Field>
                        <Field label="Fecha de lanzamiento">
                          <input
                            name="launch_date"
                            type="date"
                            defaultValue={state.settings.launch_date}
                          />
                        </Field>
                      </div>
                      <Field label="Hecho oficial que respalda la fecha">
                        <select
                          name="launch_fact_id"
                          defaultValue={state.settings.launch_fact_id}
                        >
                          <option value="">Sin configurar</option>
                          {state.facts.map((f) => (
                            <option key={f.id} value={f.id}>
                              {f.text}
                              {!f.fresh ? " · revisar" : ""}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Toggle
                        name="launch_mode"
                        checked={state.settings.launch_mode}
                      >
                        Mostrar mesa de lanzamiento en Hoy
                      </Toggle>
                      <Button disabled={busy}>Guardar configuración</Button>
                    </Form>
                    <p className="gta-muted">
                      En modo voz en off, el comentario se convierte en
                      narración con el TTS de MoneyPrinter y sus propios
                      subtítulos. Debe caber en la duración del clip. El modo
                      texto conserva el sonido original.
                    </p>
                  </Box>
                  <div className="gta-columns">
                    <Box title="Canales de Buffer">
                      <p className="gta-muted">
                        {state.buffer_configured
                          ? "Clave disponible en el backend."
                          : "Añade BUFFER_API_KEY al entorno del backend y reinícialo."}{" "}
                        Los IDs conectan cada red con su canal real. Usa una
                        cuenta profesional en Instagram.
                      </p>
                      <Button
                        variant="outline"
                        disabled={busy || !state.buffer_configured}
                        onClick={() =>
                          void act(
                            async () =>
                              setBufferChannels(
                                await gtaRequest("/buffer/channels"),
                              ),
                            "Canales consultados",
                          )
                        }
                      >
                        Consultar canales conectados
                      </Button>
                      {bufferChannels.map((c) => (
                        <p className="gta-channel" key={c.id}>
                          {c.name} · {c.service}
                          <code>{c.id}</code>
                        </p>
                      ))}
                      <Form
                        key={JSON.stringify(state.settings.buffer_channels)}
                        onSave={(d) =>
                          void act(
                            () =>
                              gtaRequest(
                                "/settings",
                                { buffer_channels: d },
                                "PATCH",
                              ),
                            "Canales asociados",
                          )
                        }
                      >
                        {state.settings.platforms.map((p) => (
                          <Field key={p} label={NETWORKS[p]}>
                            <input
                              name={p}
                              defaultValue={
                                state.settings.buffer_channels[p] || ""
                              }
                              placeholder="ID del canal en Buffer"
                            />
                          </Field>
                        ))}
                        <Button disabled={busy}>Guardar canales</Button>
                      </Form>
                      <a
                        href="https://publish.buffer.com/settings/api"
                        target="_blank"
                        rel="noreferrer"
                      >
                        Abrir Buffer <ExternalLink size={12} />
                      </a>
                    </Box>
                    <Box title="Kit de la página">
                      <p>
                        La exportación incluye avatar y banner originales,
                        playbook, hechos, banco de ideas, fuentes, calendario,
                        métricas y registro de retiradas.
                      </p>
                      <p className="gta-muted">
                        Revisa los tamaños antes de subir cada recurso. Usa un
                        nombre propio y una bio que deje clara la fan page.
                      </p>
                      <Button asChild>
                        <a href={gtaUrl("/export")}>
                          <Download />
                          Descargar GTA 6
                        </a>
                      </Button>
                      <h3>Configuración en cada red</h3>
                      <ul className="gta-checklist">
                        <li>
                          Mismo handle y bio coherente en todas las redes.
                        </li>
                        <li>
                          Instagram profesional y conectado al canal correcto.
                        </li>
                        <li>Post fijado que explique qué publicas.</li>
                        <li>
                          Verificar derechos, medios y zona horaria antes de
                          cada envío.
                        </li>
                      </ul>
                    </Box>
                  </div>
                </>
              )}
            </>
          )}
          {busy && (
            <div className="gta-busy" role="status">
              <Loader2 size={16} className="animate-spin" />
              Guardando / procesando…
            </div>
          )}
        </div>
      </PageShell>
      {state && (
        <>
          <Dialog
            open={postEditor !== null}
            onOpenChange={(open) => !open && setPostEditor(null)}
          >
            <DialogContent className="gta-dialog max-w-3xl max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>
                  {postEditor?.id ? "Editar borrador" : "Nuevo borrador GTA 6"}
                </DialogTitle>
                <DialogDescription>
                  Al guardar cambios, el post vuelve a borrador para que revises
                  la versión final.
                </DialogDescription>
              </DialogHeader>
              {postEditor && (
                <Form
                  key={postEditor.id || postEditor.title || "new"}
                  onSave={(d) =>
                    void act(async () => {
                      await gtaRequest(
                        postEditor.id ? `/posts/${postEditor.id}` : "/posts",
                        {
                          title: d.title,
                          text: d.text,
                          platform: d.platform,
                          pillar: d.pillar,
                          format: d.format,
                          source_id: d.source_id,
                          fact_ids: ids(d.fact_ids),
                          clip_id: d.clip_id,
                          hook: d.hook,
                          commentary: d.commentary,
                          spoiler: yes(d, "spoiler"),
                          reviewed: yes(d, "reviewed"),
                          media_url: d.media_url,
                          length_sec: Number(d.length_sec || 0),
                          ...((
                            postEditor as Partial<GtaPost> & {
                              idea_id?: string;
                            }
                          ).idea_id
                            ? {
                                idea_id: (postEditor as { idea_id: string })
                                  .idea_id,
                              }
                            : {}),
                        },
                        postEditor.id ? "PATCH" : "POST",
                      );
                      setPostEditor(null);
                    }, "Borrador guardado")
                  }
                >
                  <Field label="Título interno">
                    <input
                      name="title"
                      defaultValue={postEditor.title}
                      required
                    />
                  </Field>
                  <div className="gta-fields-row">
                    <Field label="Red">
                      <select
                        name="platform"
                        defaultValue={postEditor.platform || "x"}
                      >
                        {state.meta.platforms.map((p) => (
                          <option key={p} value={p}>
                            {NETWORKS[p]}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Pilar">
                      <select
                        name="pillar"
                        defaultValue={postEditor.pillar || "questions"}
                      >
                        {Object.entries(state.meta.pillars).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Formato">
                      <select
                        name="format"
                        defaultValue={postEditor.format || "text"}
                      >
                        <option value="text">Texto</option>
                        <option value="video">Video</option>
                        <option value="image">Imagen</option>
                      </select>
                    </Field>
                    <Field label="Duración (s)">
                      <input
                        name="length_sec"
                        type="number"
                        min="0"
                        step="0.01"
                        defaultValue={postEditor.length_sec || 0}
                      />
                    </Field>
                  </div>
                  <Field
                    label="Texto del post"
                    hint="X elimina hashtags; Instagram añade tres si faltan. El crédito se conserva y los límites se comprueban antes de aprobar."
                  >
                    <textarea
                      name="text"
                      defaultValue={postEditor.text}
                      rows={6}
                      required
                    />
                  </Field>
                  <Field label="Fuente original">
                    <SourceSelect
                      state={state}
                      selected={postEditor.source_id}
                    />
                  </Field>
                  <FactChoices state={state} selected={postEditor.fact_ids} />
                  <div className="gta-fields-row">
                    <Field
                      label="Gancho del clip"
                      hint="El texto quemado se elige al renderizar. Para cambiarlo en el video, crea otro corte manual con el nuevo gancho."
                    >
                      <input name="hook" defaultValue={postEditor.hook} />
                    </Field>
                    <Field label="ID de clip renderizado">
                      <input name="clip_id" defaultValue={postEditor.clip_id} />
                    </Field>
                  </div>
                  <Field label="Comentario o valor propio">
                    <textarea
                      name="commentary"
                      defaultValue={postEditor.commentary}
                    />
                  </Field>
                  <Field
                    label="Archivo público para Buffer"
                    hint="URL HTTPS directa y permanente al MP4 o imagen. No sirve una ruta local ni un enlace para compartir."
                  >
                    <input
                      name="media_url"
                      type="url"
                      defaultValue={postEditor.media_url}
                    />
                  </Field>
                  <Toggle name="spoiler" checked={postEditor.spoiler}>
                    Contiene spoiler narrativo
                  </Toggle>
                  <Toggle name="reviewed" checked={postEditor.reviewed}>
                    Revisé el texto, los hechos y el material visual de esta
                    versión
                  </Toggle>
                  <Button disabled={busy}>Guardar borrador</Button>
                </Form>
              )}
            </DialogContent>
          </Dialog>

          <Dialog
            open={sourceEditor !== null}
            onOpenChange={(open) => !open && setSourceEditor(null)}
          >
            <DialogContent className="gta-dialog max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Fuente original</DialogTitle>
                <DialogDescription>
                  El crédito identifica al autor. Registra por separado la base
                  de uso del material.
                </DialogDescription>
              </DialogHeader>
              {sourceEditor && (
                <Form
                  key={sourceEditor.id || "new"}
                  onSave={(d) =>
                    void act(async () => {
                      await gtaRequest(
                        sourceEditor.id
                          ? `/sources/${sourceEditor.id}`
                          : "/sources",
                        {
                          ...d,
                          active: yes(d, "active"),
                          monitor_enabled: yes(d, "monitor_enabled"),
                        },
                        sourceEditor.id ? "PUT" : "POST",
                      );
                      setSourceEditor(null);
                    }, "Fuente guardada")
                  }
                >
                  <Field label="Nombre">
                    <input
                      name="name"
                      defaultValue={sourceEditor.name}
                      required
                    />
                  </Field>
                  <Field label="URL original">
                    <input
                      name="url"
                      type="url"
                      defaultValue={sourceEditor.url}
                      required
                    />
                  </Field>
                  <Field
                    label="Canal para seguimiento (opcional)"
                    hint="Puedes usar su canal de YouTube si el perfil principal de Twitch o Kick no permite consultar videos."
                  >
                    <input
                      name="monitor_url"
                      type="url"
                      defaultValue={sourceEditor.monitor_url || ""}
                      placeholder="https://www.youtube.com/@creador"
                    />
                  </Field>
                  <Field label="Tipo">
                    <select
                      name="kind"
                      defaultValue={sourceEditor.kind || "creator"}
                    >
                      <option value="official">Fuente oficial</option>
                      <option value="creator">Creador</option>
                      <option value="discovery">Descubrimiento</option>
                    </select>
                  </Field>
                  <Field label="Crédito visible">
                    <input
                      name="credit"
                      defaultValue={sourceEditor.credit}
                      placeholder="@creador"
                    />
                  </Field>
                  <Field label="Permiso / base de uso">
                    <select
                      name="permission"
                      defaultValue={sourceEditor.permission || "review"}
                    >
                      <option value="review">Pendiente de revisar</option>
                      <option value="granted">
                        Uso permitido / documentado
                      </option>
                      <option value="own">Material propio</option>
                      <option value="denied">
                        Permiso denegado o retirado
                      </option>
                    </select>
                  </Field>
                  <Field label="Nota o evidencia del permiso">
                    <textarea
                      name="permission_note"
                      defaultValue={sourceEditor.permission_note}
                    />
                  </Field>
                  <Toggle name="active" checked={sourceEditor.active !== false}>
                    Fuente activa
                  </Toggle>
                  <Toggle
                    name="monitor_enabled"
                    checked={sourceEditor.monitor_enabled !== false}
                  >
                    Incluir en seguimiento automático
                  </Toggle>
                  <Button disabled={busy}>Guardar fuente</Button>
                </Form>
              )}
            </DialogContent>
          </Dialog>

          <Dialog
            open={factEditor !== null}
            onOpenChange={(open) => !open && setFactEditor(null)}
          >
            <DialogContent className="gta-dialog">
              <DialogHeader>
                <DialogTitle>Revisar un hecho</DialogTitle>
                <DialogDescription>
                  Lee la fuente oficial antes de marcar el dato como verificado.
                </DialogDescription>
              </DialogHeader>
              {factEditor && (
                <Form
                  key={factEditor.id || "new"}
                  onSave={(d) =>
                    void act(async () => {
                      await gtaRequest(
                        factEditor.id ? `/facts/${factEditor.id}` : "/facts",
                        { ...d, reviewed: yes(d, "reviewed") },
                        factEditor.id ? "PUT" : "POST",
                      );
                      setFactEditor(null);
                    }, "Hecho guardado")
                  }
                >
                  <Field label="Hecho concreto">
                    <textarea
                      name="text"
                      defaultValue={factEditor.text}
                      required
                    />
                  </Field>
                  <Field label="Página oficial que lo respalda">
                    <input
                      name="url"
                      type="url"
                      defaultValue={factEditor.url}
                      required
                    />
                  </Field>
                  {factEditor.url && (
                    <a href={factEditor.url} target="_blank" rel="noreferrer">
                      Abrir fuente <ExternalLink size={12} />
                    </a>
                  )}
                  <Field label="Estado">
                    <select
                      name="status"
                      defaultValue={factEditor.status || "unverified"}
                    >
                      <option value="unverified">Sin verificar</option>
                      <option value="verified">
                        Verificado en fuente oficial
                      </option>
                      <option value="withdrawn">Retirado / cambió</option>
                    </select>
                  </Field>
                  <Field label="Notas">
                    <input name="note" defaultValue={factEditor.note} />
                  </Field>
                  <Toggle name="reviewed">
                    Abrí la fuente oficial y comprobé que respalda este dato
                  </Toggle>
                  <Button disabled={busy}>Registrar revisión</Button>
                </Form>
              )}
            </DialogContent>
          </Dialog>

          <Dialog
            open={!!selected}
            onOpenChange={(open) => !open && setSelectedId("")}
          >
            <DialogContent className="gta-dialog max-w-3xl max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>{selected?.title || "Publicación"}</DialogTitle>
                <DialogDescription>
                  {selected
                    ? `${NETWORKS[selected.platform]} · ${STATUS[selected.status]}`
                    : ""}
                </DialogDescription>
              </DialogHeader>
              {selected && (
                <div className="gta-stack">
                  {selected.clip_id && (
                    <video
                      key={selected.clip_id}
                      src={clipsApi.rawUrl(selected.clip_id)}
                      controls
                      preload="metadata"
                      className="gta-video"
                    />
                  )}
                  <p className="gta-full-caption">{selected.text}</p>
                  {selected.clip_id && (
                    <a href={clipsApi.rawUrl(selected.clip_id)} download>
                      <Download size={14} />
                      Descargar este clip
                    </a>
                  )}
                  {selected.published_url && (
                    <a
                      href={selected.published_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Ver publicación <ExternalLink size={14} />
                    </a>
                  )}
                  {selected.issues.length > 0 && (
                    <ul className="gta-warning gta-checklist">
                      {selected.issues.map((x, i) => (
                        <li key={i}>{x}</li>
                      ))}
                    </ul>
                  )}
                  {(selected.buffer_note || selected.blocked_reason) && (
                    <p className="gta-warning">
                      {selected.buffer_note || selected.blocked_reason}
                    </p>
                  )}
                  {["draft", "approved", "planned"].includes(
                    selected.status,
                  ) && (
                    <div className="gta-actions">
                      <Button
                        variant="outline"
                        onClick={() => {
                          setSelectedId("");
                          setPostEditor(selected);
                        }}
                      >
                        Editar versión
                      </Button>
                      {selected.status === "draft" && (
                        <Button
                          disabled={busy}
                          onClick={() =>
                            void act(
                              () =>
                                gtaRequest(`/posts/${selected.id}/approve`, {}),
                              "Post aprobado",
                            )
                          }
                        >
                          <Check />
                          Aprobar
                        </Button>
                      )}
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() =>
                          void act(
                            () =>
                              gtaRequest(`/posts/${selected.id}/variants`, {
                                platforms: state.settings.platforms,
                              }),
                            "Variantes creadas como borradores",
                          )
                        }
                      >
                        Crear variantes por red
                      </Button>
                    </div>
                  )}
                  {["approved", "planned"].includes(selected.status) && (
                    <Box title="Reservar horario local">
                      <Form
                        onSave={(d) =>
                          void act(
                            () => gtaRequest(`/posts/${selected.id}/plan`, d),
                            "Horario reservado localmente",
                          )
                        }
                      >
                        <Field
                          label={`Fecha y hora · ${state.settings.timezone}`}
                        >
                          <input name="when" type="datetime-local" required />
                        </Field>
                        <Button disabled={busy}>Reservar este horario</Button>
                      </Form>
                      {selected.scheduled_at && (
                        <p>{formatTime(selected.scheduled_at)}</p>
                      )}
                    </Box>
                  )}
                  {selected.status === "planned" && (
                    <Box title="Programar en Buffer">
                      <p className="gta-muted">
                        Este botón envía el texto y los medios al canal
                        configurado de {NETWORKS[selected.platform]} para su
                        publicación el {formatTime(selected.scheduled_at)}.
                      </p>
                      <Button
                        disabled={busy || !state.buffer_configured}
                        onClick={() =>
                          void act(
                            () =>
                              gtaRequest(`/posts/${selected.id}/buffer`, {}),
                            "Programación confirmada en Buffer",
                          )
                        }
                      >
                        <Send />
                        Enviar programación a Buffer
                      </Button>
                      {!state.buffer_configured && (
                        <small>
                          Configura BUFFER_API_KEY y el canal en Configuración.
                        </small>
                      )}
                    </Box>
                  )}
                  {["pending_confirmation", "dispatching"].includes(
                    selected.status,
                  ) && (
                    <Box title="Resolver envío pendiente">
                      <p className="gta-muted">
                        Comprueba la cola de Buffer antes de elegir. Si el
                        proceso se interrumpió, espera dos minutos.
                      </p>
                      <Form
                        onSave={(d) =>
                          void act(
                            () =>
                              gtaRequest(`/posts/${selected.id}/resolve`, d),
                            "Intento resuelto",
                          )
                        }
                      >
                        <Field label="Lo que comprobaste">
                          <select name="outcome">
                            <option value="scheduled">
                              El post está programado en Buffer
                            </option>
                            <option value="not_created">
                              El post no existe en Buffer
                            </option>
                          </select>
                        </Field>
                        <Field label="ID remoto (si está programado)">
                          <input name="remote_id" />
                        </Field>
                        <Button disabled={busy}>Registrar comprobación</Button>
                      </Form>
                    </Box>
                  )}
                  {selected.platform === "youtube" &&
                    selected.clip_id &&
                    ["approved", "planned"].includes(selected.status) && (
                      <Box title="Subir con el canal de MoneyPrinter">
                        <Form
                          onSave={(d) =>
                            void act(async () => {
                              const result = await clipsApi.upload(
                                selected.clip_id,
                                {
                                  channel_id: d.channel_id,
                                  title: selected.title.slice(0, 100),
                                  description: selected.text,
                                },
                              );
                              setJob({
                                url: result.stream_url,
                                title: "GTA 6 · subir clip a YouTube",
                                upload: true,
                              });
                            }, "")
                          }
                        >
                          <Field label="Canal YouTube">
                            <select name="channel_id" required>
                              <option value="">
                                Elige el canal y revisa su perfil
                              </option>
                              {channels.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.nickname}
                                </option>
                              ))}
                            </select>
                          </Field>
                          <Button disabled={busy}>
                            Subir este clip a YouTube
                          </Button>
                        </Form>
                        <p className="gta-muted">
                          Reutiliza la sesión de Firefox del canal. Después de
                          confirmar la subida, registra el enlace publicado
                          aquí.
                        </p>
                      </Box>
                    )}
                  {[
                    "approved",
                    "planned",
                    "scheduled",
                    "pending_confirmation",
                  ].includes(selected.status) && (
                    <Box title="Confirmar una publicación real">
                      <Form
                        onSave={(d) =>
                          void act(
                            () =>
                              gtaRequest(`/posts/${selected.id}/published`, d),
                            "Publicación confirmada",
                          )
                        }
                      >
                        <Field label="URL del post publicado">
                          <input name="url" type="url" required />
                        </Field>
                        <Field
                          label={`Fecha real · ${state.settings.timezone}`}
                          hint="Vacío usa el momento actual."
                        >
                          <input name="when" type="datetime-local" />
                        </Field>
                        <Button disabled={busy}>Registrar publicación</Button>
                      </Form>
                    </Box>
                  )}
                </div>
              )}
            </DialogContent>
          </Dialog>
        </>
      )}
      <Dialog open={!!report} onOpenChange={(open) => !open && setReport("")}>
        <DialogContent className="gta-dialog max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Informe diario GTA 6</DialogTitle>
            <DialogDescription>
              Publicaciones confirmadas, lecturas comparables y asuntos
              pendientes.
            </DialogDescription>
          </DialogHeader>
          <pre className="gta-report">{report}</pre>
        </DialogContent>
      </Dialog>
      <ProgressDialog
        open={!!job}
        onOpenChange={(open) => !open && setJob(null)}
        title={job?.title || "GTA 6"}
        sseUrl={job?.url || null}
        initialPhase={job?.upload ? "upload" : "generate"}
        successMessage={
          job?.upload
            ? "Revisa el resultado de la subida y confirma el enlace publicado."
            : "Revisa los clips y borradores en GTA 6."
        }
        onDone={() => void refresh()}
      />
    </>
  );
}
