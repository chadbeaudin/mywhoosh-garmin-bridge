import hmac
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from .config import Settings
from .db import Database
from .garmin import GarminClient
from .notify import Notifier
from .processor import Processor
from .strava_web import RememberToken, StravaWeb
from .worker import Worker

log = logging.getLogger(__name__)

FIRST_TOKEN_CHECK_DELAY_SECONDS = 60


class StravaEvent(BaseModel):
    id: int
    athlete_id: int
    aspect_type: Literal["create", "update", "delete"]
    start_date: str
    device_name: str | None = None
    sport_type: str | None = None
    total_photo_count: int = 0


@dataclass(frozen=True)
class Bridge:
    settings: Settings
    db: Database
    worker: Worker


def build(settings: Settings) -> Bridge:
    db = Database(settings.db_path)
    notifier = Notifier(db, settings.telegram_bot_token, settings.telegram_chat_id)
    remember_token = RememberToken(settings.strava_remember_token, settings.strava_token_file)
    processor = Processor(
        db=db,
        garmin=GarminClient(settings.garmin_username, settings.garmin_password, settings.garmin_token_dir),
        strava_web=StravaWeb(remember_token, settings.strava_athlete_id),
        remember_token=remember_token,
        notifier=notifier,
        dry_run=settings.dry_run,
    )
    worker = Worker(db, processor, notifier, settings.token_check_interval_hours * 3600)
    return Bridge(settings, db, worker)


def create_app(bridge: Bridge) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not bridge.db.has_pending("token_check"):
            bridge.db.enqueue("token_check", {}, delay_seconds=FIRST_TOKEN_CHECK_DELAY_SECONDS)
        bridge.worker.start()
        log.info("Bridge started (dry_run=%s)", bridge.settings.dry_run)
        yield
        bridge.worker.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/events", status_code=202)
    def events(event: StravaEvent, x_bridge_secret: str = Header(default="")) -> dict:
        if not hmac.compare_digest(x_bridge_secret.encode(), bridge.settings.bridge_secret.encode()):
            raise HTTPException(status_code=401, detail="bad secret")
        if event.athlete_id != bridge.settings.strava_athlete_id:
            return {"queued": False}
        bridge.db.enqueue("strava_activity", event.model_dump())
        return {"queued": True}

    @app.get("/healthz")
    def healthz(response: Response) -> dict:
        alive = bridge.worker.is_alive()
        if not alive:
            response.status_code = 503
        return {"worker_alive": alive}

    return app
