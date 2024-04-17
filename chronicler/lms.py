from __future__ import annotations
import asyncio
import re
import json
from typing import Any
from pydantic import BaseModel
from yarl import URL
from bs4 import BeautifulSoup
from aiohttp import ClientSession
from datetime import datetime

SESSKEY_REGEX = re.compile(r'"sesskey":"(.+?)"')
INVALID_LOGIN = re.compile(r"Invalid login")

BASE_URL = URL("https://lms.sicsr.ac.in/")


class AuthFailException(BaseException): ...


class Course(BaseModel):
    id: int
    fullname: str
    shortname: str
    idnumber: str
    summary: str
    summaryformat: int
    startdate: datetime
    enddate: datetime
    visible: bool
    showactivitydates: bool
    showcompletionconditions: bool
    fullnamedisplay: str
    viewurl: str
    courseimage: str
    progress: int
    hasprogress: bool
    isfavourite: bool
    hidden: bool
    showshortname: bool
    coursecategory: str


class CoursePageMeta(BaseModel):
    id: str
    numsections: int
    sectionlist: list[str]
    editmode: bool
    highlighted: str
    maxsections: str
    baseurl: str
    statekey: str


class CmItem(BaseModel):
    id: str
    anchor: str
    name: str
    visible: bool
    sectionid: str
    sectionnumber: int
    uservisible: bool
    hascmrestrictions: bool
    accessvisible: bool
    url: str | None = None
    istrackeduser: bool
    completionstate: int | None = None


class CoursePage(BaseModel):
    course: CoursePageMeta
    section: list[Any]
    cm: list[CmItem]


class Moodle:

    def __init__(self, session_key: str, *, session: ClientSession):
        self.session_key = session_key
        self.session = session

    @classmethod
    async def init(
        cls, *, prn: str, password: str = "Student@1234", session: ClientSession
    ):
        url = BASE_URL / "login/index.php"
        async with session.get(url) as resp:
            text = await resp.text()

        def _thread():
            soup = BeautifulSoup(text, "html.parser")
            if tag := soup.select_one("#login > input:nth-child(1)"):
                token = tag.get("value")
                data = {
                    "username": prn,
                    "password": password,
                    "logintoken": token,
                }
                return data
            else:
                raise Exception("Could not login!")

        data = await asyncio.to_thread(_thread)
        async with session.post(url, data=data) as resp:
            text = await resp.text()
            if (match := SESSKEY_REGEX.search(text)) and not INVALID_LOGIN.search(text):
                return cls(str(match.group(1)), session=session)
            raise AuthFailException(f"Could not login PRN {prn}!")

    async def get_all_reports(self) -> list[Report]:
        body = [
            {
                "index": 0,
                "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                "args": {
                    "offset": 0,
                    "limit": 0,
                    "classification": "all",
                    "sort": "fullname",
                    "customfieldname": "",
                    "customfieldvalue": "",
                },
            }
        ]
        headers = {"Content-Type": "application/json"}
        url = (
            BASE_URL
            / "lib/ajax/service.php"
            % {
                "sesskey": self.session_key,
                "info": "core_course_get_enrolled_courses_by_timeline_classification",
            }
        )
        async with self.session.post(url, json=body, headers=headers) as resp:
            data = await resp.json()
        courses = [Course(**d) for d in data[0]["data"]["courses"]]
        tasks: list[asyncio.Task[Report | None]] = []
        for course in courses:
            task = asyncio.create_task(self.get_course_attendance(course))
            tasks.append(task)
        return list(filter(None, await asyncio.gather(*tasks)))

    async def get_course_attendance(self, course: Course) -> Report | None:
        body = [
            {
                "index": 0,
                "methodname": "core_courseformat_get_state",
                "args": {"courseid": course.id},
            }
        ]
        url = (
            BASE_URL
            / "lib/ajax/service.php"
            % {"sesskey": self.session_key, "info": "core_courseformat_get_state"}
        )
        async with self.session.post(url, json=body) as resp:
            data = await resp.json()
            cpm = CoursePage(**json.loads(data[0]["data"]))
        for item in cpm.cm:
            if item.name == "Attendance":
                return await Report.fetch(
                    item.id, name=course.fullnamedisplay, session=self.session
                )


class Report(BaseModel):

    name: str
    taken_sessions: str
    points_over_taken_sessions: str
    percentage_over_taken_sessions: str
    total_number_of_sessions: str
    points_over_all_sessions: str
    percentage_over_all_sessions: str
    maximum_possible_points: str
    maximum_possible_percentage: str

    @classmethod
    async def fetch(
        cls, _id: str, *, name: str, session: ClientSession
    ) -> Report | None:
        url = BASE_URL / "mod/attendance/view.php" % {"id": _id, "view": "5"}
        async with session.get(url) as resp:
            text = await resp.text()

        def _thread():
            soup = BeautifulSoup(text, "html.parser")
            if data := soup.select_one(".attlist > tbody:nth-child(1)"):
                tds = data.select("td")
                seq = list(cls.model_fields.keys())[1:]
                pack = {}
                for item, i in zip(seq, range(1, len(tds), 2)):
                    pack[item] = tds[i].text
                pack["name"] = name
                return cls(**pack)

        return await asyncio.to_thread(_thread)

    def __str__(self) -> str:
        rows: list[tuple[str, str]] = []
        for item in self.model_fields:
            clean = item.replace("_", " ").title()
            rows.append((clean, getattr(self, item, "-")))
        return "\n".join([f"{header}: {item}" for header, item in rows])

    def get_fields(self):
        return [item.replace("_", " ").title() for item in self.model_fields]

    def as_html(self) -> str:
        return (
            "<tr>"
            + "".join(f"<td>{getattr(self, i, '-')}</td>" for i in self.model_fields)
            + "</tr>"
        )
