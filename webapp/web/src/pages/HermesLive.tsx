import { useEffect, useRef, useState } from "react";
import { Radio, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { gtaRequest, gtaUrl } from "@/lib/gta6";

type Desk = { enabled: boolean; running: boolean; active: string[]; discovered?: number;
  restricted?:boolean; selected_creators?:{url:string; creator:string; language:string; account:string}[];
  community_enabled?:boolean; community_captured?:number;
  pause_reason?:string;
  youtube_accounts?: {id:string; name:string; profile_ready:boolean; assigned_clips:number; browser?:string}[];
  events?: string[]; previewing?: string;
  moments?: {id?:string; preview_ready?:boolean; url:string; title:string; start:number; end:number; captured_at:number}[];
  deliveries?: {platform:string; status:string; published_url:string; title:string; hermes_note?:string}[] };

export function HermesLive() {
  const [desk, setDesk] = useState<Desk>();
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [open, setOpen] = useState(false);
  const [playing, setPlaying] = useState("");
  const [pending, setPending] = useState(false);
  const revision = useRef(0);
  const changing = useRef(false);
  useEffect(() => {
    let mounted = true;
    let refreshing = false;
    async function refresh() {
      if (changing.current || refreshing) return;
      refreshing = true;
      const version = revision.current;
      try {
        const data = await gtaRequest<Desk>("/hermes");
        if (mounted && version === revision.current) {
          setDesk(data);
          setPollError("");
        }
      } catch (e) {
        if (mounted && version === revision.current) setPollError(String(e));
      } finally { refreshing = false; }
    }
    void refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => { mounted = false; clearInterval(timer); };
  }, []);
  async function toggle() {
    if (!desk || changing.current) return;
    changing.current = true;
    revision.current++;
    if (!desk.enabled) setOpen(true);
    setPending(true);
    setError("");
    try {
      setDesk(await gtaRequest<Desk>("/hermes", {enabled: !desk.enabled}));
      setPollError("");
    } catch (e) { setError(String(e)); }
    finally { changing.current = false; setPending(false); }
  }
  const status = pending ? (desk?.enabled ? "Deteniendo Hermes…" : "Iniciando Hermes…")
    : pollError ? "Sin conexión con MoneyPrinter" : desk?.running ? "Activo"
    : desk?.enabled ? "Esperando al agente" : desk ? "Pausado" : "Consultando estado…";
  return <section className="gta-box">
    <div className="gta-box-heading"><h2>Hermes · Directos</h2><span>{status}</span></div>
    {desk?.pause_reason && <p role="status">{desk.pause_reason}</p>}
    <p>{desk?.restricted ? `Graba únicamente los ${desk.selected_creators?.length ?? 0} creadores seleccionados. Los demás canales y directorios están excluidos. Clips verticales sin franjas difuminadas.` : "Busca directos de GTA VI en Twitch, YouTube y Kick. Graba por turnos y envía los mejores momentos de fuentes habilitadas a Clips."}</p>
    {!!desk?.youtube_accounts?.length && <div className="my-3 rounded-lg border border-border/30 p-3 text-sm">
      <p className="font-semibold">Reparto de clips entre cuentas de YouTube</p>
      <p>{desk.restricted ? "La cuenta depende del creador y del idioma de sus subtítulos." : "Cada clip se asigna a una sola cuenta. Con dos cuentas, 10 clips se reparten 5 y 5."}</p>
      {desk.youtube_accounts.map(c => <p key={c.id}>{c.name} · {c.assigned_clips} clips asignados · {c.browser === "chrome" ? "Subidas realizadas desde Chrome" : c.profile_ready ? "Firefox listo para subir" : "Firefox pendiente de asociar"}</p>)}
    </div>}
    {!!desk?.selected_creators?.length && <ul className="mb-3 text-sm">
      {desk.selected_creators.map(c => <li key={c.url}>{c.creator} → {c.account} · Subtítulos {c.language === "en" ? "en inglés" : "en español"}</li>)}
    </ul>}
    {desk?.community_enabled && <p>Incluye clips de sus comunidades de las últimas 24 horas, con crédito al creador y al clipper. {desk.community_captured ?? 0} clips de comunidad capturados.</p>}
    <Dialog open={open} onOpenChange={setOpen}>
      <div className="flex flex-wrap gap-2">
        <Button disabled={pending || !desk} onClick={toggle}>{desk?.enabled ? "Detener Hermes" : "Iniciar Hermes y publicar clips"}</Button>
        <DialogTrigger asChild><Button variant="outline">Ver actividad de Hermes</Button></DialogTrigger>
      </div>
      <DialogContent className="max-h-[90dvh] max-w-3xl grid-rows-[auto_minmax(0,1fr)_auto]">
        <DialogHeader className="pr-9">
          <DialogTitle className="flex items-center gap-2"><Radio className="size-5 text-primary" />Actividad de Hermes</DialogTitle>
          <DialogDescription>El monitor se actualiza cada 4 segundos. Puedes cerrarlo mientras Hermes trabaja.</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 space-y-4 overflow-y-auto text-sm">
          <div role="status" className="font-semibold">{status}</div>
          {(error || pollError) && <p role="alert" className="text-destructive">{error || pollError}</p>}
          {desk && <p>{desk.discovered ?? 0} canales encontrados · {desk.active.length} intentos de captura en curso</p>}
          {!!desk?.moments?.length && <section className="space-y-2">
            <h3 className="font-semibold">Cortes para revisar</h3>
            {playing && <video className="mx-auto block h-[min(58dvh,640px)] w-auto max-w-full aspect-[9/16] rounded-lg bg-black" key={playing} controls autoPlay
              aria-label="Reproductor del corte local" src={gtaUrl("/hermes/previews/"+playing+"?encuadre=vertical")} />}
            <ul className="max-h-52 space-y-2 overflow-y-auto">
              {desk.moments.map(m => <li key={m.id || m.url+m.captured_at} className="flex items-center justify-between gap-2">
                <span>{m.title} · {m.end-m.start} s<br/><a className="break-all text-xs underline" href={m.url} target="_blank" rel="noreferrer">{m.url}</a></span>
                {m.preview_ready && m.id ? <Button size="sm" variant="outline" onClick={() => setPlaying(m.id!)}>Ver corte</Button>
                  : <span className="text-xs text-muted-foreground">{m.id && desk.previewing === m.id ? "Recortando…" : "En cola"}</span>}
              </li>)}
            </ul>
          </section>}
          {!!desk?.active.length && <section><h3 className="font-semibold">Intentando capturar</h3>
            <ul className="break-all text-muted-foreground">{desk.active.map(url => <li key={url}>{url}</li>)}</ul></section>}
          <section className="rounded-xl border border-border/20 bg-background/70 p-4">
            <h3 className="mb-2 flex items-center gap-2 font-semibold"><Terminal className="size-4" />Registro de actividad</h3>
            {desk?.events?.length ? <ol className="max-h-56 space-y-1 overflow-y-auto break-words font-mono text-xs leading-relaxed">
              {desk.events.map((event, i) => <li key={i}>{event}</li>)}
            </ol> : <p className="text-muted-foreground">Aún no hay eventos registrados.</p>}
          </section>
          {!!desk?.deliveries?.length && <section><h3 className="font-semibold">Entregas por red</h3>
            {desk.deliveries.map((d,i) => <p key={i}>{d.platform} · {d.title} · {d.published_url
              ? <a className="underline" href={d.published_url} target="_blank" rel="noreferrer">{d.status}</a> : d.hermes_note || d.status}</p>)}</section>}
        </div>
        <DialogFooter><Button variant="outline" onClick={() => setOpen(false)}>Cerrar monitor</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    {!open && (error || pollError) && <p role="alert">{error || pollError}</p>}
    {desk?.enabled && <p>{desk.discovered || 0} canales encontrados por Hermes · {desk.active.length} canales intentando capturar</p>}
  </section>;
}
