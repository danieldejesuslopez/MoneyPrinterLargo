import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Clapperboard, Download, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { gtaRequest, gtaUrl, apiRoot, type GtaSnapshot } from "@/lib/gta6";

interface Channel {
  id: string;
  nickname: string;
  youtube_handle: string;
  profile_ready: boolean;
  pending_identity: boolean;
  firefox_profile: string;
  upload_disabled_reason: string;
  managed: boolean;
}
type Props = {
  state: GtaSnapshot;
  onChange: () => Promise<void>;
  onJob: (job: { url: string; title: string; upload?: boolean }) => void;
};

export function Gta6News({ state, onChange, onJob }: Props) {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [profiles, setProfiles] = useState<
    { name: string; path: string; exists: boolean }[]
  >([]);
  const [channelId, setChannelId] = useState(
    state.settings.youtube_channel_id || "",
  );
  const [autoUpload, setAutoUpload] = useState(
    state.settings.batch_auto_upload || false,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [facts, setFacts] = useState<string[]>([]);
  const [binding, setBinding] = useState<Channel | null>(null);
  const reload = useCallback(async () => {
    try {
      const [rows, paths] = await Promise.all([
        gtaRequest<Channel[]>("/channels"),
        gtaRequest<typeof profiles>("/firefox-profiles"),
      ]);
      setChannels(rows);
      setProfiles(paths);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);
  const channel = channels.find((c) => c.id === channelId);
  async function action(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    try {
      await work();
      await onChange();
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e), {
        duration: 9000,
      });
    } finally {
      setBusy(false);
    }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void action(async () => {
      const job = await gtaRequest<{ stream_url: string }>("/news", {
        channel_id: channelId,
        count: Number(data.get("count")),
        text_posts: Number(data.get("text_posts")),
        fact_ids: facts,
        auto_upload: autoUpload,
        allow_reuse: data.get("allow_reuse") === "on",
      });
      onJob({
        url: apiRoot + job.stream_url,
        title: "GTA 6 · lote de noticias narradas",
      });
    });
  }
  const stage: Record<string, string> = {
    queued: "En cola",
    running: "En proceso",
    rendering: "Renderizando",
    rendered: "Video listo",
    uploaded: "Publicado",
    pending_confirmation: "Revisar subida",
    done: "Lote listo",
    error: "Requiere atención",
  };
  return (
    <div className="gta-stack">
      <div className="gta-columns">
        <section className="gta-box">
          <div className="gta-box-heading">
            <h2>Generar lote del día</h2>
            <Clapperboard size={20} />
          </div>
          <p className="gta-muted">
            Crea noticias narradas con tarjetas originales, subtítulos y textos
            por red. La narración conserva literalmente los hechos verificados.
          </p>
          {error && (
            <p className="gta-warning" role="alert">
              {error}
            </p>
          )}
          <form className="gta-form" onSubmit={submit}>
            <label className="gta-field">
              <span>Canal del lote</span>
              <select
                value={channelId}
                onChange={(e) => {
                  setChannelId(e.target.value);
                  setAutoUpload(false);
                }}
                required
              >
                <option value="">Selecciona el canal de GTA 6</option>
                {channels.map((c) => (
                  <option key={c.id} value={c.id} disabled={c.pending_identity}>
                    {c.nickname}
                    {c.pending_identity ? " · falta identificar" : ""}
                  </option>
                ))}
              </select>
            </label>
            <div className="gta-fields-row">
              <label className="gta-field">
                <span>Videos narrados</span>
                <input
                  name="count"
                  type="number"
                  defaultValue="2"
                  min="1"
                  max="5"
                  required
                />
              </label>
              <label className="gta-field">
                <span>Posts de texto para X</span>
                <input
                  name="text_posts"
                  type="number"
                  defaultValue="3"
                  min="0"
                  max="6"
                  required
                />
              </label>
            </div>
            <fieldset className="gta-facts">
              <legend>Hechos para narrar</legend>
              <small>
                Sin selección, el lote elige hechos vigentes todavía no usados.
              </small>
              {state.facts.map((f) => (
                <label key={f.id} className="gta-check">
                  <input
                    type="checkbox"
                    disabled={!f.fresh}
                    checked={facts.includes(f.id)}
                    onChange={(e) =>
                      setFacts((old) =>
                        e.target.checked
                          ? [...old, f.id]
                          : old.filter((id) => id !== f.id),
                      )
                    }
                  />
                  <span>
                    {f.text}
                    <small>{f.fresh ? "Vigente" : "Revisar fuente"}</small>
                  </span>
                </label>
              ))}
            </fieldset>
            <label className="gta-check">
              <input type="checkbox" name="allow_reuse" />
              <span>Regenerar hechos usados en otros lotes</span>
            </label>
            <label className="gta-check">
              <input
                type="checkbox"
                checked={autoUpload}
                disabled={!channel?.profile_ready}
                onChange={(e) => setAutoUpload(e.target.checked)}
              />
              <span>
                Subir los videos a YouTube al terminar
                <small>
                  {channel?.profile_ready
                    ? "Este lote autoriza la subida tras las comprobaciones automáticas de hechos y render."
                    : "Confirma primero el canal y su perfil de Firefox. Puedes generar borradores ahora."}
                </small>
              </span>
            </label>
            <Button disabled={busy || !channel || channel.pending_identity}>
              {busy ? <Loader2 className="animate-spin" /> : <Clapperboard />}
              Generar lote del día
            </Button>
          </form>
        </section>
        <div className="gta-stack">
          <section className="gta-box">
            <div className="gta-box-heading">
              <h2>Kit del canal</h2>
            </div>
            <p className="gta-muted">
              Avatar y banner PNG con la identidad guardada en Configuración. Se
              descargan listos para cargarlos en YouTube Studio.
            </p>
            <img
              src={gtaUrl("/brand/banner")}
              alt={`Banner de ${state.settings.name}`}
              className="rounded-lg w-full mb-4"
            />
            <div className="gta-actions">
              <Button variant="outline" size="sm" asChild>
                <a href={gtaUrl("/brand/avatar")} download>
                  <Download />
                  Avatar
                </a>
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a href={gtaUrl("/brand/banner")} download>
                  <Download />
                  Banner
                </a>
              </Button>
            </div>
            <p className="gta-muted">
              {state.settings.name} · {state.settings.handle}
              <br />
              {state.settings.bio}
              <br />
              Zona horaria: {state.settings.timezone}
            </p>
          </section>
          <section className="gta-box">
            <div className="gta-box-heading">
              <h2>Canales de MoneyPrinter</h2>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void reload()}
                aria-label="Actualizar canales"
              >
                <RefreshCw />
              </Button>
            </div>
            {channels
              .filter((c) => c.managed)
              .map((c) => (
                <div className="gta-log" key={c.id}>
                  <strong>{c.nickname}</strong>
                  <small>
                    {c.youtube_handle || "Handle pendiente"} ·{" "}
                    {c.profile_ready
                      ? "Perfil confirmado"
                      : "Subida sin habilitar"}
                  </small>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setBinding(c)}
                  >
                    Configurar perfil
                  </Button>
                </div>
              ))}
            {!channels.some((c) => c.managed) && (
              <p className="gta-muted">
                Registra un canal en Canales YouTube y selecciona aquí el canal
                del lote.
              </p>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={busy || !channel || channel.pending_identity}
              onClick={() =>
                void action(async () => {
                  await gtaRequest(
                    "/settings",
                    {
                      youtube_channel_id: channelId,
                      batch_auto_upload: autoUpload,
                    },
                    "PATCH",
                  );
                  toast.success("Canal preferido guardado");
                })
              }
            >
              Guardar canal preferido
            </Button>
          </section>
        </div>
      </div>
      {binding && (
        <section className="gta-box">
          <div className="gta-box-heading">
            <h2>Perfil de {binding.nickname}</h2>
            <Button variant="ghost" size="sm" onClick={() => setBinding(null)}>
              Cerrar
            </Button>
          </div>
          <form
            className="gta-form"
            key={binding.id}
            onSubmit={(e) => {
              e.preventDefault();
              const fd = new FormData(e.currentTarget);
              void action(async () => {
                await gtaRequest(
                  `/channels/${binding.id}/profile`,
                  {
                    name: String(fd.get("name")),
                    handle: String(fd.get("handle")),
                    profile: String(fd.get("profile")),
                    confirmed: fd.get("confirmed") === "on",
                  },
                  "PUT",
                );
                setBinding(null);
                toast.success("Perfil asociado al canal");
              });
            }}
          >
            <div className="gta-fields-row">
              <label className="gta-field">
                <span>Nombre real del canal</span>
                <input
                  name="name"
                  defaultValue={
                    binding.pending_identity ? "" : binding.nickname
                  }
                  required
                />
              </label>
              <label className="gta-field">
                <span>Handle real</span>
                <input
                  name="handle"
                  defaultValue={binding.youtube_handle}
                  placeholder="@tu_canal"
                  required
                />
              </label>
            </div>
            <label className="gta-field">
              <span>Perfil de Firefox</span>
              <input
                name="profile"
                list="gta-firefox-profiles"
                defaultValue={binding.firefox_profile}
                required
              />
              <datalist id="gta-firefox-profiles">
                {profiles.map((p) => (
                  <option key={p.path} value={p.path}>
                    {p.name}
                  </option>
                ))}
              </datalist>
            </label>
            <p className="gta-muted">
              La lista de perfiles contiene nombres y rutas; no identifica la
              cuenta de YouTube. Comprueba que la sesión del perfil pertenece al
              handle indicado.
            </p>
            <label className="gta-check">
              <input name="confirmed" type="checkbox" required />
              <span>
                Comprobé que esta sesión de Firefox corresponde a este canal
              </span>
            </label>
            <Button disabled={busy}>Asociar perfil y habilitar subidas</Button>
          </form>
        </section>
      )}
      <section className="gta-box">
        <div className="gta-box-heading">
          <h2>Lotes recientes</h2>
        </div>
        {state.news_jobs?.length ? (
          state.news_jobs
            .slice(-8)
            .reverse()
            .map((job) => (
              <div className="gta-log" key={job.id}>
                <strong>
                  {job.channel_name} · {stage[job.status] || job.status}
                </strong>
                <small>
                  {job.items.length} videos · {job.text_post_ids.length} posts
                  de texto ·{" "}
                  {job.auto_upload
                    ? "Con subida automática"
                    : "Borradores para revisar"}
                </small>
                {job.items.map((item) => (
                  <small key={item.key}>
                    {stage[item.status] || item.status}
                    {item.error ? ` · ${item.error}` : ""}
                  </small>
                ))}
                {job.error && <p className="gta-warning">{job.error}</p>}
                {job.status === "done" && !job.auto_upload && (
                  <div className="gta-actions">
                    <Button size="sm" variant="outline" asChild>
                      <a href="/gta6?tab=studio&view=posts">
                        Revisar borradores
                      </a>
                    </Button>
                    <Button
                      size="sm"
                      disabled={
                        busy ||
                        !channels.find((c) => c.id === job.channel_id)
                          ?.profile_ready
                      }
                      onClick={() =>
                        void action(async () => {
                          const next = await gtaRequest<{ stream_url: string }>(
                            `/news/${job.id}/publish`,
                            {},
                          );
                          onJob({
                            url: apiRoot + next.stream_url,
                            title: `GTA 6 · subir lote de ${job.channel_name}`,
                            upload: true,
                          });
                        })
                      }
                    >
                      Subir los {job.items.length} videos a YouTube
                    </Button>
                  </div>
                )}
                {job.status === "error" && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void action(async () => {
                        const next = await gtaRequest<{ stream_url: string }>(
                          `/news/${job.id}/resume`,
                          {},
                        );
                        onJob({
                          url: apiRoot + next.stream_url,
                          title: "GTA 6 · continuar lote",
                        });
                      })
                    }
                  >
                    Continuar sin repetir terminados
                  </Button>
                )}
              </div>
            ))
        ) : (
          <p className="gta-muted">
            Aquí aparecerán el progreso y los resultados del primer lote.
          </p>
        )}
      </section>
    </div>
  );
}
