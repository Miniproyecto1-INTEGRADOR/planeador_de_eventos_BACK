import os
import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(
    title="Planificador de eventos API",
    version="1.0.0",
    description="API para gestionar eventos y subtareas logísticas",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


EVENTOS: dict[str, dict[str, Any]] = {}
SUBTAREAS: dict[str, dict[str, Any]] = {}
USUARIOS: dict[str, dict[str, Any]] = {
    "demo@demo.com": {"id": "user-demo", "email": "demo@demo.com", "password": "123456"},
}
DAILY_LIMITS: dict[str, int] = {"user-demo": 6}
EVENT_NOT_FOUND = "Evento no encontrado."
VALID_STATUSES = {"pending", "done", "postponed"}


def _coerce_date(value: str | date | datetime | None) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="La fecha no tiene un formato válido.") from exc


def _sort_key_for_target(item: dict[str, Any]) -> tuple[int, str, int]:
    target = item.get("target_date") or "9999-12-31"
    try:
        target_day = datetime.strptime(str(target)[:10], "%Y-%m-%d").date()
    except ValueError:
        target_day = date(9999, 12, 31)

    if target_day < date.today():
        priority = 0
    elif target_day == date.today():
        priority = 1
    else:
        priority = 2
    return (priority, str(target_day), item.get("estimated_minutes", 0))


def _normalize_status(value: str | None) -> str:
    status = (value or "pending").strip().lower()
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="El estado debe ser pending, done o postponed.")
    return status


def _event_payload(evento: "EventCreate") -> dict[str, Any]:
    name = (evento.name or "").strip()
    event_type = (evento.event_type or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="El nombre del evento es obligatorio.")
    if not event_type:
        raise HTTPException(status_code=400, detail="El tipo de evento es obligatorio.")

    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "event_type": event_type,
        "event_date": evento.event_date,
        "color": evento.color,
        "user_id": evento.user_id,
        "created_at": datetime.now(timezone.utc),
    }


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1)
    event_date: datetime
    color: str | None = None
    user_id: str | None = None


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    event_type: str | None = None
    event_date: datetime | None = None
    color: str | None = None
    user_id: str | None = None


class SubtaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1)
    description: str | None = None
    target_date: str | None = None
    estimated_minutes: int = Field(..., gt=0)
    status: str = Field(default="pending")


class SubtaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    target_date: str | None = None
    estimated_minutes: int | None = None
    status: str | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    event_type: str
    event_date: datetime
    color: str | None = None
    user_id: str | None = None
    created_at: datetime


class SubtaskOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    event_id: str
    task_id: str | None = None
    title: str
    description: str | None = None
    target_date: str | None = None
    estimated_minutes: int
    status: str = "pending"
    created_at: datetime


@app.post("/api/login/")
def login(email: str, password: str):
    usuario = USUARIOS.get((email or "").strip().lower())
    if not usuario or usuario["password"] != (password or ""):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    return {"token": f"demo-token-{usuario['id']}", "user_id": usuario["id"], "email": usuario["email"]}


@app.get("/api/usuarios/{user_id}/limite")
def get_daily_limit(user_id: str):
    return {"user_id": user_id, "daily_limit_hours": DAILY_LIMITS.get(user_id, 6)}


@app.put("/api/usuarios/{user_id}/limite")
def set_daily_limit(user_id: str, value: int):
    if value < 1 or value > 16:
        raise HTTPException(status_code=400, detail="El límite diario debe estar entre 1 y 16 horas.")
    DAILY_LIMITS[user_id] = value
    return {"user_id": user_id, "daily_limit_hours": value}


@app.get("/api/health/")
def health_check():
    return {
        "status": "ok",
        "message": "API funcionando correctamente",
        "events_count": len(EVENTOS),
        "subtasks_count": len(SUBTAREAS),
    }


@app.get("/api/hoy/")
def today_summary():
    all_subtasks = list(SUBTAREAS.values())
    today = date.today()

    vencidas = []
    hoy = []
    proximas = []

    for item in all_subtasks:
        if item.get("status") == "done":
            continue
        target = item.get("target_date")
        if not target:
            proximas.append(item)
            continue
        target_day = _coerce_date(target)
        if target_day is None:
            proximas.append(item)
            continue
        if target_day < today:
            vencidas.append(item)
        elif target_day == today:
            hoy.append(item)
        else:
            proximas.append(item)

    vencidas.sort(key=lambda item: (_coerce_date(item.get("target_date")) or date.max, item.get("estimated_minutes", 0)))
    hoy.sort(key=lambda item: (_coerce_date(item.get("target_date")) or date.max, item.get("estimated_minutes", 0)))
    proximas.sort(key=lambda item: (_coerce_date(item.get("target_date")) or date.max, item.get("estimated_minutes", 0)))

    return {"vencidas": vencidas, "hoy": hoy, "proximas": proximas}


@app.get("/api/eventos/", response_model=list[EventOut])
def list_eventos():
    return list(EVENTOS.values())


@app.get("/api/eventos/{event_id}", response_model=EventOut)
@app.get("/api/eventos/{event_id}/", response_model=EventOut)
def get_event(event_id: str):
    evento = EVENTOS.get(event_id)
    if not evento:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)
    return evento


@app.get("/api/eventos/{event_id}/progreso")
def get_event_progress(event_id: str):
    if event_id not in EVENTOS:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)

    items = [item for item in SUBTAREAS.values() if item["event_id"] == event_id]
    total = len(items)
    done = sum(1 for item in items if item.get("status") == "done")
    percent = 0 if total == 0 else round((done / total) * 100, 2)
    return {"event_id": event_id, "total": total, "done": done, "percent": percent}


@app.post("/api/eventos/", response_model=EventOut, status_code=201)
def create_evento(evento: EventCreate):
    payload = _event_payload(evento)
    EVENTOS[payload["id"]] = payload
    return payload


@app.patch("/api/eventos/{event_id}", response_model=EventOut)
def update_event(event_id: str, changes: EventUpdate):
    evento = EVENTOS.get(event_id)
    if not evento:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)

    data = changes.model_dump(exclude_unset=True)
    if not data:
        return evento

    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="El nombre del evento es obligatorio.")
        evento["name"] = name
    if "event_type" in data:
        event_type = (data["event_type"] or "").strip()
        if not event_type:
            raise HTTPException(status_code=400, detail="El tipo de evento es obligatorio.")
        evento["event_type"] = event_type
    if "event_date" in data:
        evento["event_date"] = data["event_date"]
    if "color" in data:
        evento["color"] = data["color"]
    if "user_id" in data:
        evento["user_id"] = data["user_id"]

    return evento


@app.delete("/api/eventos/{event_id}")
def delete_event(event_id: str):
    evento = EVENTOS.pop(event_id, None)
    if not evento:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)

    for subtask_id, subtask in list(SUBTAREAS.items()):
        if subtask.get("event_id") == event_id:
            del SUBTAREAS[subtask_id]

    return {"message": "Evento eliminado correctamente.", "event_id": event_id}


@app.post("/api/eventos/{event_id}/subtareas/", response_model=SubtaskOut, status_code=201)
def create_subtask(event_id: str, subtask: SubtaskCreate):
    if event_id not in EVENTOS:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)

    title = (subtask.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="El título de la gestión logística es obligatorio.")
    if subtask.estimated_minutes <= 0:
        raise HTTPException(status_code=400, detail="Las horas estimadas deben ser mayores que 0.")

    normalized_status = _normalize_status(subtask.status)
    subtask_id = str(uuid.uuid4())
    payload = {
        "id": subtask_id,
        "event_id": event_id,
        "task_id": None,
        "title": title,
        "description": subtask.description,
        "target_date": subtask.target_date,
        "estimated_minutes": subtask.estimated_minutes,
        "status": normalized_status,
        "created_at": datetime.now(timezone.utc),
    }
    SUBTAREAS[subtask_id] = payload
    return payload


@app.get("/api/eventos/{event_id}/subtareas/", response_model=list[SubtaskOut])
def list_subtasks(event_id: str):
    if event_id not in EVENTOS:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)
    return [item for item in SUBTAREAS.values() if item["event_id"] == event_id]


@app.patch("/api/eventos/{event_id}/subtareas/{subtask_id}", response_model=SubtaskOut)
def update_subtask(event_id: str, subtask_id: str, changes: SubtaskUpdate):
    if event_id not in EVENTOS:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)

    subtask = SUBTAREAS.get(subtask_id)
    if not subtask or subtask["event_id"] != event_id:
        raise HTTPException(status_code=404, detail="Subtarea no encontrada.")

    data = changes.model_dump(exclude_unset=True)
    if not data:
        return subtask

    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="El título es obligatorio.")
        subtask["title"] = title
    if "description" in data:
        subtask["description"] = data["description"]
    if "target_date" in data:
        subtask["target_date"] = data["target_date"]
    if "estimated_minutes" in data:
        minutes = data["estimated_minutes"]
        if minutes is None or minutes <= 0:
            raise HTTPException(status_code=400, detail="Las horas estimadas deben ser mayores que 0.")
        subtask["estimated_minutes"] = minutes
    if "status" in data:
        subtask["status"] = _normalize_status(data["status"])

    return subtask


@app.delete("/api/eventos/{event_id}/subtareas/{subtask_id}")
def delete_subtask(event_id: str, subtask_id: str):
    if event_id not in EVENTOS:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)

    subtask = SUBTAREAS.get(subtask_id)
    if not subtask or subtask["event_id"] != event_id:
        raise HTTPException(status_code=404, detail="Subtarea no encontrada.")

    del SUBTAREAS[subtask_id]
    return {"message": "Subtarea eliminada correctamente.", "subtask_id": subtask_id}
