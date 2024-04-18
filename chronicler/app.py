import re
import time
from datetime import datetime, date, time as dtime
from pathlib import Path
from typing import Annotated, Any
from aiohttp import ClientSession
from pydantic import BaseModel
from litestar import Controller, Request, Response, get, post
from litestar.datastructures import State
from litestar.response import Template
from litestar import Litestar
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.template.config import TemplateConfig
from litestar.config.cors import CORSConfig
from litestar.config.allowed_hosts import AllowedHostsConfig
from litestar.static_files import create_static_files_router
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.middleware.session.server_side import ServerSideSessionConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.stores.file import FileStore
from litestar.exceptions import HTTPException

from .core import TimetableClient, Entry
from .lms import Moodle


class Payload(BaseModel):
    start: date | None = None
    end: date | None = None
    types: dict[str, list[str]]  # type: [regex]

    def __init__(self, **data: Any):
        super().__init__(**data)

    def get_start(self) -> date:
        return self.start or datetime.now().date()

    def get_end(self) -> date:
        return self.end or datetime.now().date()


class TimetableResponse(BaseModel):
    entries: dict[date, list[dict[str, Any]]]
    time: float
    fields: list[str] = Entry.dump_fields


class MoodleLogin(BaseModel):
    username: str
    password: str


class FreeClassesForm(BaseModel):
    time: dtime | None = None
    room: str


class FreeClassesPayload(BaseModel):
    time: float
    entries: dict[int, list[tuple[str, str]]]


class MainController(Controller):

    path = "/"

    template = "index.html"

    @get()
    async def home(self, request: Request, state: State) -> Template:
        return Template(
            self.template,
            context={
                "types": await state.client.get_types(),
                "preload": request.session or {"types": []},
            },
        )

    @post()
    async def fetch_table(
        self,
        request: Request,
        state: State,
        data: Payload,
    ) -> TimetableResponse:
        start = time.perf_counter()
        request.set_session(data)
        entries: dict[date, list[Entry]] = {}
        items = await state.client.fetch(start=data.get_start(), end=data.get_end())
        for item in items:
            bucket = entries.setdefault(item.start.date(), [])
            if item.type in data.types:
                filters = data.types[item.type]
                if not filters:
                    bucket.append(item)
                for filt in filters:
                    if re.search(filt, item.full_desc):
                        bucket.append(item)
                        break
            elif not data.types:  # if no types are specified, render the whole table
                bucket.append(item)
        for bucket in entries.values():
            bucket.sort(key=lambda e: e.start)

        response: dict[date, list[dict[str, Any]]] = {}
        for e, b in sorted(entries.items(), key=lambda t: t[0]):
            response[e] = [i.dump() for i in b]

        return TimetableResponse(entries=response, time=time.perf_counter() - start)

    @get("/about")
    async def about(self) -> Template:
        return Template("about.html")

    @get("/lms")
    async def attendance_login(self) -> Template:
        return Template("report.html")

    @post("/lms")
    async def render_attendance_report(
        self,
        data: Annotated[MoodleLogin, Body(media_type=RequestEncodingType.URL_ENCODED)],
    ) -> Template:
        start = time.perf_counter()
        async with ClientSession() as session:
            moodle = await Moodle.init(
                prn=data.username, password=data.password, session=session
            )
            reports = await moodle.get_all_reports()
            return Template(
                "report.html",
                context=dict(reports=reports, time=time.perf_counter() - start),
            )

    @post("/fc")
    async def render_free_classrooms(
        self, state: State, data: FreeClassesForm
    ) -> FreeClassesPayload:
        start = time.perf_counter()
        now = datetime.now()
        entries: list[Entry] = await state.client.fetch(start=now, end=now)
        rooms: dict[int, list[Entry]] = {}
        for entry in entries:
            bucket = rooms.setdefault(entry.room, [])
            bucket.append(entry)
        for bucket in rooms.values():
            bucket.sort(key=lambda e: e.start)

        free: dict[int, list[tuple[datetime, datetime]]] = {}
        for room, bucket in rooms.items():
            if not bucket:
                continue
            free_bucket = free.setdefault(room, [])
            start_hour = bucket[0].start
            if start_hour.hour != 7:
                free_bucket.append(
                    (
                        datetime.now().replace(
                            hour=7, minute=30, second=0, microsecond=0
                        ),
                        start_hour,
                    )
                )
            for i in range(len(bucket) - 1):
                entry = bucket[i]
                next_entry = bucket[i + 1]
                if entry.end < next_entry.start:
                    free_bucket.append((entry.end, next_entry.start))
            last_hour = bucket[-1].end
            if last_hour.hour != 20:
                free_bucket.append(
                    (
                        last_hour,
                        datetime.now().replace(
                            hour=20, minute=30, second=0, microsecond=0
                        ),
                    )
                )
        sorted_entries: dict[int, list[tuple[str, str]]] = {}
        for room, bucket in sorted(free.items(), key=lambda t: t[0]):
            f = "%H:%M"
            if data.room == "All" or room == int(data.room):
                new = []
                for s, e in bucket:
                    if data.time is None or (s.time() <= data.time <= e.time()):
                        new.append((s.strftime(f), e.strftime(f)))
                sorted_entries[room] = new

        return FreeClassesPayload(
            time=time.perf_counter() - start, entries=sorted_entries
        )

    @get("/fc")
    async def free_classrooms(self, state: State) -> Template:
        return Template("fc.html", context={"classes": await state.client.get_rooms()})


async def init_http_session(app: Litestar):
    session = ClientSession()
    app.state.http = session
    app.state.client = TimetableClient(session=session)


async def close_http_session(app: Litestar):
    if (session := app.state.http) and not session.closed:
        await session.close()


def http_error_handler(request: Request, exc: HTTPException) -> Template | Response:
    if request.method == "GET":
        return Template("error.html", context={"request": request, "exc": exc})
    return Response(
        content=exc.detail,
        status_code=exc.status_code,
    )


cors_config = CORSConfig(
    allow_origins=[
        "https://chr.zeffo.me",
        "https://chronicler.up.railway.app",
        "127.0.0.1",
        "localhost",
        "192.168.1.254",
    ]
)
allowed_hosts = AllowedHostsConfig(
    allowed_hosts=[
        "chronicler.up.railway.app",
        "chr.zeffo.me",
        "localhost",
        "127.0.0.1",
        "192.168.1.254",
    ]
)

rate_limit_conf = RateLimitConfig(("minute", 30))

app = Litestar(
    route_handlers=[
        MainController,
        create_static_files_router(path="/static", directories=["static"]),
    ],
    template_config=TemplateConfig(
        directory=Path("templates"),
        engine=JinjaTemplateEngine,
    ),
    on_startup=[init_http_session],
    on_shutdown=[close_http_session],
    cors_config=cors_config,
    allowed_hosts=allowed_hosts,
    middleware=[ServerSideSessionConfig().middleware, rate_limit_conf.middleware],
    stores={"sessions": FileStore(path=Path("session_data"))},
    exception_handlers={HTTPException: http_error_handler},
)
