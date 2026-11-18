import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ExternalLink, Loader2, Play, RefreshCw, Save, Search } from "lucide-react";
import { toast } from "sonner";
import { HermesLive } from "./HermesLive";
import { Button } from "@/components/ui/button";
import { apiRoot, gtaRequest, type GtaSnapshot, type GtaSource } from "@/lib/gta6";

type Config = {
  enabled: boolean;
  interval_minutes: number;
  sources_per_scan: number;
  videos_per_source: number;
  lookback_hours: number;
  max_productions_per_day: number;
  clips_per_video: number;
  layout: string;
  min_seconds: number;
  max_seconds: number;
  use_llm: boolean;
  commentary: string;
};
type Candidate = {
  id: string;
  source_id: string;
  title: string;
  url: string;
  published_at: string;
  published_at_estimated?: boolean;
  discovered_at: string;
  status: string;
  request_id: string;
  clip_ids?: string[];
  post_ids?: string[];
  error?: string;
};
type Scan = {
  id: string;
  started_at: string;
  finished_at?: string;
  status: string;
  discovered?: number;
  produced?: number;
  error?: string;
  sources?: {
    source_id: string;
    name: string;
    status: string;
    found?: number;
    new?: number;
    error?: string;
  }[];
};
type Snapshot = {
  config: Config;
  candidates: Candidate[];
  scans: Scan[];
  last_scan_at: string;
  next_scan_at: string;
  last_error: string;
  running?: boolean;
  dispatch_until?: string;
  stats?: {
    eligible_sources?: number;
    pending_permission?: number;
    produced_today?: number;
    counts?: Record<string, number>;
  };
};
type Props = {
  onChange: () => Promise<void>;
  onJob: (job: { url: string; title: string; upload?: boolean }) => void;
};

const LABELS: Record<string, string> = {
  needs_permission: "Revisar permiso",
  ready: "Listo para producir",
  queued: "En cola",
  producing: "Produciendo",
  done: "Borradores creados",
  error: "Requiere atención",
  ignored: "Descartado",
};
const defaults: Config = {
  enabled: false,
  interval_minutes: 60,
  sources_per_scan: 12,
  videos_per_source: 3,
  lookback_hours: 168,
  max_productions_per_day: 2,
  clips_per_video: 1,
  layout: "blur",
  min_seconds: 7,
  max_seconds: 30,
  use_llm: true,
  commentary: "¿Qué harías tú en este momento de GTA?",
};

export function Gta6Autopilot({ onChange, onJob }: Props) {
  const [data, setData] = useState<Snapshot | null>(null);
  const [form, setForm] = useState<Config>(defaults);
  const [sources, setSources] = useState<GtaSource[]>([]);
  const [timezone, setTimezone] = useState("America/La_Paz");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [manualPending, setManualPending] = useState(false);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const initialized = useRef(false);
  const scanBaseline = useRef("");
  const reload = useCallback(async (resetForm = false) => {
    try {
      const next = await gtaRequest<Snapshot>("/autopilot");
      setData(next);
      if (!initialized.current || resetForm) {
        setForm({ ...defaults, ...next.config });
        initialized.current = true;
      }
      const reservationActive = !!next.dispatch_until &&
        new Date(next.dispatch_until).valueOf() > Date.now();
      if (!next.running &&
          ((next.last_scan_at && next.last_scan_at !== scanBaseline.current) || !reservationActive)) {
        setManualPending(false);
      }
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
    let active = true;
    gtaRequest<GtaSnapshot>().then((state) => {
      if (active) {
        setSources(state.sources);
        setTimezone(state.settings.timezone);
      }
    }).catch(() => {});
    return () => { active = false; };
  }, [reload]);

  const running = data?.running || false;
  useEffect(() => {
    if (!data?.config.enabled && !manualPending && !running) return;
    const timer = window.setInterval(() => { void reload(); }, 15000);
    return () => window.clearInterval(timer);
  }, [data?.config.enabled, manualPending, running, reload]);

  function formatTime(value?: string) {
    if (!value) return "Sin programar";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return value;
    return new Intl.DateTimeFormat("es", {
      timeZone: timezone, dateStyle: "medium", timeStyle: "short",
    }).format(date);
  }
  async function action(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    try {
      await work();
      await reload();
      await onChange();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e), { duration: 9000 });
    } finally {
      setBusy(false);
    }
  }
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void action(async () => {
      await gtaRequest("/autopilot/config", form, "PATCH");
      await reload(true);
      toast.success(form.enabled ? "Seguimiento automático activado" : "Seguimiento automático pausado");
    });
  }
  function start(produce: boolean) {
    void action(async () => {
      const job = await gtaRequest<{ stream_url: string }>("/autopilot/run", { produce });
      scanBaseline.current = data?.last_scan_at || "";
      setManualPending(true);
      onJob({ url: apiRoot + job.stream_url, title: produce ? "GTA 6 · ciclo automático" : "GTA 6 · revisar creadores" });
    });
  }
  function updateNumber(name: keyof Config, value: string) {
    setForm((previous) => ({ ...previous, [name]: value === "" ? "" : Number(value) } as Config));
  }
  const sourceName = (id: string) => sources.find((source) => source.id === id)?.name || "Creador registrado";
  const candidates = [...(data?.candidates || [])].reverse().filter((candidate) =>
    (filter === "all" || candidate.status === filter) &&
    `${candidate.title} ${sourceName(candidate.source_id)}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );

  return (
    <div className="gta-stack">
      <HermesLive />
      <div className="gta-section-heading">
        <div>
          <h1>De tu lista a nuevos borradores.</h1>
          <p>Revisa creadores por turnos, encuentra videos recientes de GTA y alimenta al productor sin repetir videos.</p>
        </div>
        <Button variant="outline" disabled={busy} onClick={() => void reload()}><RefreshCw size={15} />Actualizar</Button>
      </div>
      {error && <p className="gta-warning" role="alert">{error}</p>}
      {data?.last_error && <p className="gta-warning" role="status">Último ciclo: {data.last_error}</p>}
      <div className="gta-columns">
        <section className="gta-box">
          <div className="gta-box-heading"><h2>Seguimiento programado</h2><span>{data?.config.enabled ? "Activo" : "Pausado"}</span></div>
          <p className="gta-muted">Funciona mientras el backend de MoneyPrinter está abierto y tiene conexión a Internet. Cada ciclo crea borradores para revisar.</p>
          <form className="gta-form" onSubmit={save}>
            <label className="gta-check">
              <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
              <span>Activar seguimiento y producción programados<small>Guarda los cambios para aplicar esta opción.</small></span>
            </label>
            <div className="gta-fields-row">
              <label className="gta-field"><span>Revisar cada cuántos minutos</span><input type="number" min="15" max="1440" required value={form.interval_minutes} onChange={(e) => updateNumber("interval_minutes", e.target.value)} /></label>
              <label className="gta-field"><span>Videos fuente a producir por día</span><input type="number" min="0" max="20" required value={form.max_productions_per_day} onChange={(e) => updateNumber("max_productions_per_day", e.target.value)} /><small>0 revisa novedades sin producir. Zona: {timezone}.</small></label>
            </div>
            <label className="gta-field"><span>Comentario propio para los clips</span><textarea required value={form.commentary} onChange={(e) => setForm({ ...form, commentary: e.target.value })} /><small>Usa una pregunta o comentario que no invente hechos sobre la escena.</small></label>
            <details>
              <summary>Fuentes y formato del lote</summary>
              <div className="gta-form" style={{ marginTop: 12 }}>
                <div className="gta-fields-row">
                  <label className="gta-field"><span>Creadores por ciclo</span><input type="number" min="1" max="100" required value={form.sources_per_scan} onChange={(e) => updateNumber("sources_per_scan", e.target.value)} /></label>
                  <label className="gta-field"><span>Videos recientes por creador</span><input type="number" min="1" max="20" required value={form.videos_per_source} onChange={(e) => updateNumber("videos_per_source", e.target.value)} /></label>
                </div>
                <div className="gta-fields-row">
                  <label className="gta-field"><span>Antigüedad máxima en horas</span><input type="number" min="1" max="720" required value={form.lookback_hours} onChange={(e) => updateNumber("lookback_hours", e.target.value)} /></label>
                  <label className="gta-field"><span>Clips por video fuente</span><input type="number" min="1" max="3" required value={form.clips_per_video} onChange={(e) => updateNumber("clips_per_video", e.target.value)} /></label>
                </div>
                <div className="gta-fields-row">
                  <label className="gta-field"><span>Duración mínima del clip</span><input type="number" min="5" max="179" required value={form.min_seconds} onChange={(e) => updateNumber("min_seconds", e.target.value)} /></label>
                  <label className="gta-field"><span>Duración máxima del clip</span><input type="number" min="6" max="180" required value={form.max_seconds} onChange={(e) => updateNumber("max_seconds", e.target.value)} /></label>
                </div>
                <label className="gta-field"><span>Encuadre</span><select value={form.layout} onChange={(e) => setForm({ ...form, layout: e.target.value })}><option value="blur">Video completo con fondo desenfocado</option><option value="crop">Recorte vertical</option><option value="facecam">Gameplay y cámara</option></select></label>
                <label className="gta-check"><input type="checkbox" checked={form.use_llm} onChange={(e) => setForm({ ...form, use_llm: e.target.checked })} /><span>Usar el modelo configurado para seleccionar momentos y ganchos</span></label>
              </div>
            </details>
            <Button disabled={busy || !data}>{busy ? <Loader2 className="animate-spin" /> : <Save size={15} />}Guardar configuración</Button>
          </form>
        </section>
        <section className="gta-box">
          <div className="gta-box-heading"><h2>Próximo ciclo</h2>{running && <Loader2 className="animate-spin" size={17} />}</div>
          <dl className="gta-stack">
            <div><dt className="gta-muted">Última revisión</dt><dd>{formatTime(data?.last_scan_at)}</dd></div>
            <div><dt className="gta-muted">Siguiente revisión</dt><dd>{data?.config.enabled ? formatTime(data.next_scan_at) : "Seguimiento pausado"}</dd></div>
            <div><dt className="gta-muted">Creadores activos para seguimiento</dt><dd>{data?.stats?.eligible_sources ?? "—"}</dd></div>
            <div><dt className="gta-muted">Videos fuente procesados hoy</dt><dd>{data?.stats?.produced_today ?? 0} / {data?.config.max_productions_per_day ?? 0}</dd></div>
            <div><dt className="gta-muted">Videos pendientes de permiso</dt><dd>{data?.stats?.pending_permission ?? data?.candidates.filter((candidate) => candidate.status === "needs_permission").length ?? 0}</dd></div>
          </dl>
          <div className="gta-actions" style={{ marginTop: 20 }}>
            <Button variant="outline" disabled={busy || running || manualPending || !data} onClick={() => start(false)}><Search size={15} />Escanear ahora</Button>
            <Button disabled={busy || running || manualPending || !data?.config.enabled} onClick={() => start(true)}><Play size={15} />Ejecutar ciclo</Button>
          </div>
          <p className="gta-muted" style={{ marginTop: 16 }}>El escaneo consulta metadatos públicos. La producción utiliza fuentes con permiso o base de uso registrada y crea borradores. Los canales sin acceso compatible aparecen en el historial para corregir su enlace de seguimiento.</p>
          <a href="?tab=sources" className="text-sm underline">Revisar fuentes y permisos</a>
        </section>
      </div>
      <section className="gta-box">
        <div className="gta-box-heading"><h2>Videos encontrados</h2><span>{candidates.length}</span></div>
        <div className="gta-fields-row">
          <label className="gta-field"><span>Buscar título o creador</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Rubius, GTA Online…" /></label>
          <label className="gta-field"><span>Estado</span><select value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">Todos</option>{Object.entries(LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        </div>
        {!candidates.length && <p className="gta-muted">Todavía no hay videos que coincidan. El primer escaneo buscará publicaciones recientes de GTA en tu lista.</p>}
        <div className="gta-table-wrap">
          {!!candidates.length && <table>
            <thead><tr><th>Video y creador</th><th>Publicación</th><th>Estado</th><th>Acciones</th></tr></thead>
            <tbody>{candidates.map((candidate) => <tr key={candidate.id}>
              <td><a href={candidate.url} target="_blank" rel="noreferrer">{candidate.title} <ExternalLink size={12} className="inline" /></a><small className="block gta-muted">{sourceName(candidate.source_id)}</small>{candidate.error && <small className="block gta-warning">{candidate.error}</small>}</td>
              <td>{formatTime(candidate.published_at)}{candidate.published_at_estimated && <small className="block gta-muted">Fecha aproximada</small>}</td>
              <td>{LABELS[candidate.status] || candidate.status}{candidate.clip_ids?.length ? <small className="block gta-muted">{candidate.clip_ids.length} clips · {candidate.post_ids?.length || 0} borradores</small> : null}{candidate.request_id && <small className="block gta-muted">Trabajo {candidate.request_id}</small>}</td>
              <td><div className="gta-actions">
                {candidate.status === "error" && <Button variant="outline" size="sm" disabled={busy || running} onClick={() => void action(async () => { await gtaRequest(`/autopilot/candidates/${candidate.id}/retry`, {}); toast.success("Video preparado para el próximo ciclo"); })}>Volver a la cola</Button>}
                {["needs_permission", "ready", "error"].includes(candidate.status) && <Button variant="ghost" size="sm" disabled={busy || running} onClick={() => void action(async () => { await gtaRequest(`/autopilot/candidates/${candidate.id}/ignore`, {}); })}>Descartar</Button>}
              </div></td>
            </tr>)}</tbody>
          </table>}
        </div>
      </section>
      <section className="gta-box">
        <div className="gta-box-heading"><h2>Historial de revisiones</h2></div>
        {!data?.scans.length && <p className="gta-muted">Las revisiones mostrarán qué creadores se consultaron y cualquier problema de acceso.</p>}
        {[...(data?.scans || [])].reverse().map((scan) => <details key={scan.id} className="gta-post">
          <summary>{formatTime(scan.started_at)} · {scan.status === "running" ? "En curso" : scan.status === "error" ? "Con incidencias" : "Finalizada"} · {scan.discovered || 0} videos nuevos · {scan.produced || 0} fuentes producidas</summary>
          {scan.error && <p className="gta-warning">{scan.error}</p>}
          <ul className="gta-stack" style={{ marginTop: 12 }}>{(scan.sources || []).map((source) => <li key={source.source_id}><strong>{source.name || sourceName(source.source_id)}</strong> · {source.status === "ok" ? `${source.found || 0} encontrados, ${source.new || 0} nuevos` : source.status === "unsupported" ? "Enlace no compatible" : "Error de consulta"}{source.error && <small className="block gta-warning">{source.error}</small>}</li>)}</ul>
        </details>)}
      </section>
    </div>
  );
}
