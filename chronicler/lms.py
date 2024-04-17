import asyncio
from pydantic import BaseModel
from yarl import URL
from bs4 import BeautifulSoup
from aiohttp import ClientSession

BASE_URL = URL("https://lms.sicsr.ac.in/mod/attendance/view.php?view=5")


def build_url(_id: int) -> URL:
    return BASE_URL % {"id": str(_id)}


class Moodle(BaseModel):

    def __init__(self, session_cookie: str):
        self.session_cookie = session_cookie

    @classmethod
    async def init(
        cls, *, prn: str, password: str = "Student@1234", session: ClientSession
    ):
        async with session.get("https://lms.sicsr.ac.in/login/index.php") as resp:
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

        try:
            data = await asyncio.to_thread(_thread)
            async with session.post(
                "https://lms.sicsr.ac.in/login/index.php", data=data
            ) as resp:
                session_cookie = resp.cookies.get("MoodleSession")  # type: ignore
                return cls(str(session_cookie))
        except Exception as e:
            raise e


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
    async def fetch(cls, _id: int, *, name: str, session: ClientSession) -> "Report":
        async with session.get(build_url(_id)) as resp:
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
            else:
                raise Exception(f"Could not fetch report for ID: {_id}!")

        return await asyncio.to_thread(_thread)

    def __str__(self) -> str:
        rows: list[tuple[str, str]] = []
        for item in self.model_fields:
            clean = item.replace("_", " ").title()
            rows.append((clean, getattr(self, item, "-")))
        return "\n".join([f"{header}: {item}" for header, item in rows])
