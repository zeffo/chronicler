import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Annotated
from aiohttp import ClientSession
from litestar import Controller, get, post
from litestar.datastructures import State
from litestar.response import Template
from litestar import Litestar
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.template.config import TemplateConfig
from litestar.static_files import create_static_files_router
from litestar.enums import RequestEncodingType
from litestar.params import Body
from pydantic import BaseModel, BeforeValidator

from .core import fetch, Entry


class Type(BaseModel):
    name: str  # name of the type
    rules: list[str]  # regex rules to apply to the type


class Config(BaseModel):
    types: dict[str, Type]


class ConfigData(BaseModel):
    date: datetime
    config: Annotated[Config, BeforeValidator(lambda s: tomllib.loads(s))]


class MainController(Controller):

    path = "/"

    template = "index.html"

    @get()
    async def home(self, state: State) -> Template:
        return Template(self.template)

    @post()
    async def render_table(
        self,
        state: State,
        data: Annotated[ConfigData, Body(media_type=RequestEncodingType.URL_ENCODED)],
    ) -> Template:
        report = list(await fetch(start=data.date, end=data.date, session=state.http))
        types = data.config.types.values()
        found: list[Entry] = []
        for entry in report:
            for t in types:
                if re.search(t.name, entry.type):
                    if t.rules and not any(
                        [re.search(rgx, entry.full_desc) for rgx in t.rules]
                    ):
                        continue
                    found.append(entry)
        entries = [item.dump() for item in found]
        context = {"entries": entries} if entries else {"error": "No classes found!"}
        return Template(
            self.template,
            context=context,
        )

    @get("/about")
    async def about(self) -> Template:
        return Template("about.html")


async def init_http_session(app: Litestar):
    app.state.http = ClientSession()


async def close_http_session(app: Litestar):
    if (session := app.state.http) and not session.closed:
        await session.close()


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
)
