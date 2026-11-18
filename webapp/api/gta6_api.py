"""GTA 6 workspace API; long production jobs use MoneyPrinter's job registry."""

from __future__ import annotations

from functools import wraps
import json
import re
from typing import Any, Callable, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from gta6 import Workspace
from gta6 import clipping, channels as gta_channels, news, graphics, autopilot
from gta6.scheduler import AutopilotScheduler
from gta6.buffer import BufferClient, BufferError, dispatch, resolve


class WorkspaceRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        @wraps(original)
        async def handler(request: Request):
            try:
                return await original(request)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            except (ValueError, clipping_error()) as exc:
                raise HTTPException(400, str(exc)) from exc
            except BufferError as exc:
                raise HTTPException(502, str(exc)) from exc
            except BlockingIOError as exc:
                raise HTTPException(409, "El productor automático ya está ocupado") from exc
        return handler


def clipping_error():
    from clips.pipeline import ClipError
    return ClipError


router = APIRouter(prefix="/api/gta6", tags=["GTA 6"], route_class=WorkspaceRoute)
workspace = Workspace()
try:
    from . import hermes_control
except ImportError:
    import hermes_control


class HermesSwitch(BaseModel):
    enabled: bool


@router.get("/hermes")
def hermes_status():
    return hermes_control.status(workspace)


@router.get("/hermes/previews/{recording_id}")
def hermes_preview(recording_id: str):
    from gta6.hermes_live import configured, permitted
    if not re.fullmatch(r"[0-9a-f]{32}", recording_id):
        raise HTTPException(404, "Corte no disponible")
    manifest = workspace.store.directory / "hermes" / (recording_id + ".json")
    path = manifest.with_suffix(".mp4")
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "La grabación ya no está disponible")
    _, excluded = configured(workspace.store.read())
    if not path.is_file() or not record.get("moment") or record.get("url") in excluded or not permitted(workspace.store.read(), record.get("url", "")):
        raise HTTPException(404, "Corte no disponible")
    return FileResponse(path, media_type="video/mp4")


@router.post("/hermes")
def hermes_toggle(data: HermesSwitch, request: Request):
    return hermes_control.toggle(workspace, data.enabled, request)

_spawn_job: Callable[..., Any] | None = None
_autopilot_scheduler = None


def bind_jobs(spawn):
    global _spawn_job
    _spawn_job = spawn


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceIn(Input):
    name: str = Field(min_length=1, max_length=200)
    url: str
    monitor_url: str = ""
    monitor_enabled: bool = True
    kind: Literal["official", "creator", "discovery"]
    credit: str = ""
    permission: Literal["review", "granted", "denied", "own"] = "review"
    permission_note: str = ""
    active: bool = True


class FactIn(Input):
    text: str = Field(min_length=1, max_length=4000)
    url: str
    status: Literal["verified", "unverified", "withdrawn"] = "unverified"
    reviewed: bool = False
    note: str = ""


class IdeaIn(Input):
    title: str = Field(min_length=1, max_length=300)
    pillar: str = "questions"
    angle: str = ""
    fact_ids: list[str] = Field(default_factory=list)


class GenerateIn(Input):
    count: int = Field(default=15, ge=1, le=30)
    use_llm: bool = True


class PostIn(Input):
    title: str | None = None
    text: str | None = None
    platform: str | None = None
    pillar: str | None = None
    format: Literal["text", "video", "image"] | None = None
    source_id: str | None = None
    fact_ids: list[str] | None = None
    clip_id: str | None = None
    hook: str | None = None
    commentary: str | None = None
    spoiler: bool | None = None
    synthetic: bool | None = None
    reviewed: bool | None = None
    media_url: str | None = None
    idea_id: str | None = None
    length_sec: float | None = Field(default=None, ge=0, le=10800)
    test_id: str | None = None

    def changes(self):
        return self.model_dump(exclude_unset=True, exclude_none=True)


class SettingsIn(Input):
    name: str | None = None
    handle: str | None = None
    timezone: str | None = None
    language: str | None = None
    platforms: list[str] | None = None
    angle: str | None = None
    bio: str | None = None
    voice: str | None = None
    voice_mode: Literal["text", "voiceover"] | None = None
    avoid_creators: str | None = None
    approval_hours: str | None = None
    daily_x: int | None = Field(default=None, ge=1, le=20)
    daily_video: int | None = Field(default=None, ge=1, le=10)
    slots: list[str] | None = None
    buffer_channels: dict[str, str] | None = None
    launch_date: str | None = None
    launch_fact_id: str | None = None
    kickoff_start: str | None = None
    launch_mode: bool | None = None
    youtube_channel_id: str | None = None
    hermes_youtube_channel_ids: list[str] | None = None
    hermes_routes: dict[str, dict[str, str]] | None = None
    hermes_layout: Literal["crop", "facecam"] | None = None
    hermes_platforms: list[str] | None = None
    hermes_community_enabled: bool | None = None
    hermes_min_seconds: int | None = Field(default=None, ge=7, le=120)
    hermes_max_seconds: int | None = Field(default=None, ge=7, le=120)
    batch_auto_upload: bool | None = None


class PlanIn(Input):
    when: str


class DayIn(Input):
    day: str


class PlatformsIn(Input):
    platforms: list[str]


class PublishedIn(Input):
    url: str
    when: str = ""


class ResolveIn(Input):
    outcome: Literal["scheduled", "not_created"]
    remote_id: str = ""


class MetricIn(Input):
    hours: Literal[12, 24, 72]
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    saves: int | None = Field(default=None, ge=0)
    bookmarks: int | None = Field(default=None, ge=0)
    follows: int | None = Field(default=None, ge=0)
    retention: float | None = Field(default=None, ge=0, le=100)


class NoteIn(Input):
    kind: str = ""
    text: str = ""
    evidence: str = ""
    name: str = ""
    url: str = ""
    formats: str = ""
    hooks: str = ""
    lesson: str = ""
    question: str = ""
    answer: str = ""
    variable: str = ""
    control: str = ""
    variant: str = ""


class TextIn(Input):
    text: str = Field(min_length=1)


class CheckIn(Input):
    key: str = Field(min_length=1, max_length=200)
    done: bool


class ClipIn(Input):
    source_id: str
    target: str
    mode: Literal["auto", "cut", "full"] = "auto"
    count: int = Field(default=3, ge=1, le=20)
    min_seconds: float = Field(default=7, ge=5, le=179)
    max_seconds: float = Field(default=30, ge=6, le=180)
    layout: Literal["blur", "crop", "facecam"] = "blur"
    facecam: str = ""
    language: str = ""
    spans: list[str] = Field(default_factory=list)
    titles: list[str] = Field(default_factory=list)
    subtitles: bool = True
    use_llm: bool = True
    commentary: str = Field(min_length=1)
    fact_ids: list[str] = Field(default_factory=list)
    platform: str = "youtube"
    spoiler: bool = False


class NewsIn(Input):
    channel_id: str = ""
    count: int = Field(default=2, ge=1, le=5)
    text_posts: int = Field(default=3, ge=0, le=6)
    fact_ids: list[str] = Field(default_factory=list)
    auto_upload: bool = False
    allow_reuse: bool = False


class RegisterChannelIn(Input):
    name: str = Field(min_length=1, max_length=80)
    handle: str


class BindChannelIn(Input):
    profile: str
    confirmed: bool
    name: str = ""
    handle: str = ""


class AutopilotConfigIn(Input):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=1440)
    sources_per_scan: int | None = Field(default=None, ge=1, le=100)
    videos_per_source: int | None = Field(default=None, ge=1, le=20)
    lookback_hours: int | None = Field(default=None, ge=1, le=720)
    max_productions_per_day: int | None = Field(default=None, ge=0, le=20)
    clips_per_video: int | None = Field(default=None, ge=1, le=3)
    min_seconds: float | None = Field(default=None, ge=5, le=179)
    max_seconds: float | None = Field(default=None, ge=6, le=180)
    layout: Literal["blur", "crop", "facecam"] | None = None
    commentary: str | None = Field(default=None, min_length=1, max_length=2000)
    use_llm: bool | None = None


class AutopilotRunIn(Input):
    produce: bool = True


def autopilot_scheduler():
    global _autopilot_scheduler
    if _spawn_job is None:
        raise HTTPException(503, "El sistema de trabajos todavía no está listo")
    if _autopilot_scheduler is None or _autopilot_scheduler.workspace is not workspace:
        _autopilot_scheduler = AutopilotScheduler(workspace, lambda args, **kwargs: _spawn_job(args, **kwargs))
    return _autopilot_scheduler


@router.get("/autopilot")
def automatic_status():
    return autopilot.snapshot(workspace)


@router.patch("/autopilot/config")
def automatic_config(data: AutopilotConfigIn):
    return autopilot.configure(workspace, data.model_dump(exclude_unset=True, exclude_none=True))


@router.post("/autopilot/run")
def automatic_run(data: AutopilotRunIn):
    job = autopilot_scheduler().dispatch(produce=data.produce)
    return {"job_id": job.id, "stream_url": f"/api/jobs/{job.id}/stream"}


@router.post("/autopilot/candidates/{candidate_id}/retry")
def automatic_retry(candidate_id: str):
    return autopilot.retry(workspace, candidate_id)


@router.post("/autopilot/candidates/{candidate_id}/ignore")
def automatic_ignore(candidate_id: str):
    return autopilot.ignore(workspace, candidate_id)


@router.get("")
def snapshot():
    return workspace.snapshot()


@router.patch("/settings")
def settings(data: SettingsIn):
    return workspace.settings(data.model_dump(exclude_none=True, exclude_unset=True))


@router.post("/sources")
def source(data: SourceIn):
    return workspace.source(data.model_dump())


@router.put("/sources/{row_id}")
def update_source(row_id: str, data: SourceIn):
    return workspace.source(data.model_dump(exclude_unset=True), row_id)


@router.post("/sources/{row_id}/takedown")
def takedown(row_id: str, data: TextIn):
    return workspace.takedown(row_id, data.text)


@router.post("/facts")
def fact(data: FactIn):
    return workspace.fact(data.model_dump())


@router.put("/facts/{row_id}")
def update_fact(row_id: str, data: FactIn):
    return workspace.fact(data.model_dump(), row_id)


@router.post("/ideas")
def idea(data: IdeaIn):
    return workspace.idea(data.model_dump())


@router.post("/ideas/generate")
def generate(data: GenerateIn):
    return workspace.generate_ideas(data.count, data.use_llm)


@router.post("/posts")
def post(data: PostIn):
    return workspace.post(data.changes())


@router.patch("/posts/{row_id}")
def update_post(row_id: str, data: PostIn):
    return workspace.post(data.changes(), row_id)


@router.post("/posts/{row_id}/variants")
def variants(row_id: str, data: PlatformsIn):
    return workspace.variants(row_id, data.platforms)


@router.post("/posts/{row_id}/approve")
def approve(row_id: str):
    return workspace.approve(row_id)


@router.post("/posts/{row_id}/plan")
def plan(row_id: str, data: PlanIn):
    return workspace.plan(row_id, data.when)


@router.post("/calendar/plan-day")
def plan_day(data: DayIn):
    return workspace.day_plan(data.day)


@router.post("/posts/{row_id}/published")
def published(row_id: str, data: PublishedIn):
    return workspace.published(row_id, data.url, data.when)


@router.post("/posts/{row_id}/metrics")
def metric(row_id: str, data: MetricIn):
    return workspace.metric(row_id, data.hours, data.model_dump())


@router.post("/notes/{collection}")
def note(collection: str, data: NoteIn):
    return workspace.note(collection, data.model_dump())


@router.post("/engagement/{row_id}/idea")
def question_idea(row_id: str):
    return workspace.question_to_idea(row_id)


@router.post("/experiments/{row_id}/complete")
def complete_experiment(row_id: str, data: TextIn):
    return workspace.finish_experiment(row_id, data.text)


@router.put("/checks")
def check(data: CheckIn):
    return workspace.check(data.key, data.done)


@router.get("/buffer/channels")
def buffer_channels():
    return BufferClient().channels()


@router.post("/posts/{row_id}/buffer")
def send_buffer(row_id: str):
    return dispatch(workspace, row_id)


@router.post("/posts/{row_id}/resolve")
def resolve_buffer(row_id: str, data: ResolveIn):
    return resolve(workspace, row_id, data.outcome, data.remote_id)


@router.post("/clips")
def clips(data: ClipIn):
    if _spawn_job is None:
        raise HTTPException(503, "El sistema de trabajos todavía no está listo")
    row = clipping.request(workspace, data.model_dump())
    try:
        job = _spawn_job(["gta6", "clips", "--request-id", row["id"]], title="GTA 6 · crear clips")
    except Exception:
        with workspace.store.edit() as state:
            from gta6.service import find
            find(state["clip_jobs"], row["id"]).update(status="error", error="No se pudo iniciar el trabajo")
        raise
    return {"job_id": job.id, "request_id": row["id"], "stream_url": f"/api/jobs/{job.id}/stream"}


@router.get("/export")
def export():
    return Response(workspace.export(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="GTA-6.zip"'})


@router.get("/report", response_class=PlainTextResponse)
def report():
    return workspace.daily_report()


@router.get("/channels")
def channels():
    return gta_channels.listing()


@router.get("/firefox-profiles")
def firefox_profiles():
    return gta_channels.profiles()


@router.post("/channels")
def register_channel(data: RegisterChannelIn):
    return gta_channels.register(data.name, data.handle)


@router.put("/channels/{channel_id}/profile")
def bind_channel(channel_id: str, data: BindChannelIn):
    return gta_channels.bind(channel_id, **data.model_dump())


def _start_news(request_id: str):
    if _spawn_job is None:
        raise HTTPException(503, "El sistema de trabajos todavía no está listo")
    try:
        job = _spawn_job(["gta6", "news", "--request-id", request_id], title="GTA 6 · lote de noticias narradas")
    except Exception:
        from gta6.service import find
        with workspace.store.edit() as state:
            find(state["news_jobs"], request_id).update(status="error", error="No se pudo iniciar el trabajo")
        raise
    return {"job_id": job.id, "request_id": request_id, "stream_url": f"/api/jobs/{job.id}/stream"}


@router.post("/news")
def news_batch(data: NewsIn):
    if _spawn_job is None:
        raise HTTPException(503, "El sistema de trabajos todavía no está listo")
    row = news.request(workspace, data.model_dump())
    return _start_news(row["id"])


@router.post("/news/{request_id}/resume")
def resume_news(request_id: str):
    from gta6.service import find
    row = find(workspace.store.read()["news_jobs"], request_id)
    if row["status"] == "done":
        raise ValueError("Este lote ya está completo")
    return _start_news(request_id)


@router.post("/news/{request_id}/publish")
def publish_news(request_id: str):
    if _spawn_job is None:
        raise HTTPException(503, "El sistema de trabajos todavía no está listo")
    news.publish_rendered(workspace, request_id)
    return _start_news(request_id)


@router.get("/brand/{asset}")
def brand_asset(asset: str):
    from fastapi.responses import FileResponse
    if asset not in ("avatar", "banner"):
        raise LookupError("Recurso desconocido")
    settings = workspace.store.read()["settings"]
    paths = graphics.brand(workspace.store.directory / "brand", settings["name"], settings["handle"])
    return FileResponse(paths[asset], media_type="image/png", filename=f"gta6-{asset}.png")
