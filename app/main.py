import json
import os
from contextvars import ContextVar
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://cqhgezigsatbkujljehu.supabase.co").rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
SUPABASE_ACCESS_TOKEN = ContextVar("supabase_access_token", default=None)

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


@app.middleware("http")
async def forward_supabase_session(request: Request, call_next):
    token = SUPABASE_ACCESS_TOKEN.set(request.headers.get("authorization", ""))
    try:
        return await call_next(request)
    finally:
        SUPABASE_ACCESS_TOKEN.reset(token)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


def _supabase_request(
    table: str,
    method: str = "GET",
    filters: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
        raise HTTPException(status_code=503, detail="Configura SUPABASE_URL y SUPABASE_PUBLISHABLE_KEY en back/.env.")
    query = urllib.parse.urlencode(filters or {})
    url = f"{SUPABASE_URL}/rest/v1/{table}" + (f"?{query}" if query else "")
    body = json.dumps(payload, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)).encode() if payload is not None else None
    headers = {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    authorization = SUPABASE_ACCESS_TOKEN.get()
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            response_body = response.read()
            return json.loads(response_body) if response_body else []
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            detail = error_body.get("message") or error_body.get("details") or "Supabase rechazó la solicitud."
            if error_body.get("code") == "23505" or "EMAIL_ALREADY_REGISTERED" in detail:
                raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese correo.") from exc
        except HTTPException:
            raise
        except Exception:
            detail = "Supabase rechazó la solicitud."
        raise HTTPException(status_code=exc.code, detail=detail) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="No fue posible contactar el Data API de Supabase.") from exc


def _get_rows(table: str, filters: dict[str, str] | None = None, select: str = "*") -> list[dict[str, Any]]:
    return _supabase_request(table, filters={"select": select, **(filters or {})})


def _get_row(table: str, filters: dict[str, str], select: str = "*") -> dict[str, Any] | None:
    rows = _get_rows(table, {**filters, "limit": "1"}, select)
    return rows[0] if rows else None


def _insert_row(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = _supabase_request(table, method="POST", payload=payload)
    if not rows:
        raise HTTPException(status_code=502, detail="Supabase no devolvió el registro creado.")
    return rows[0]


def _update_row(table: str, filters: dict[str, str], payload: dict[str, Any]) -> dict[str, Any] | None:
    rows = _supabase_request(table, method="PATCH", filters=filters, payload=payload)
    return rows[0] if rows else None


def _delete_rows(table: str, filters: dict[str, str]) -> list[dict[str, Any]]:
    return _supabase_request(table, method="DELETE", filters=filters)


def _supabase_rpc(function_name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return _supabase_request(f"rpc/{function_name}", method="POST", payload=payload)


def _supabase_auth_request(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{SUPABASE_URL}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            detail = error_body.get("msg") or error_body.get("message") or error_body.get("error_description") or "Supabase Auth rechazó la solicitud."
        except Exception:
            detail = "Supabase Auth rechazó la solicitud."
        status = 401 if exc.code in (400, 401) and "credential" in detail.lower() else exc.code
        raise HTTPException(status_code=status, detail=detail) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="No fue posible contactar Supabase Auth.") from exc


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


EVENT_NOT_FOUND = "Evento no encontrado."
VALID_STATUSES = {"pending", "done", "postponed"}


def _coerce_date(value: str | date | datetime | None) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="La fecha no tiene un formato válido.") from exc


def _normalize_status(value: str | None) -> str:
    status = (value or "pending").strip().lower()
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="El estado debe ser pending, done o postponed.")
    return status


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
    status: str = "pending"


class SubtaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    description: str | None = None
    target_date: str | None = None
    estimated_minutes: int | None = None
    status: str | None = None


class UserCreate(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=6, max_length=72)


class LoginRequest(BaseModel):
    email: str
    password: str


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


def _create_event_payload(evento: EventCreate) -> dict[str, Any]:
    name = evento.name.strip()
    event_type = evento.event_type.strip()
    if not name or not event_type:
        raise HTTPException(status_code=400, detail="El nombre y tipo del evento son obligatorios.")
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "event_type": event_type,
        "event_date": evento.event_date.isoformat(),
        "color": evento.color,
        "user_id": evento.user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/login/")
def login(credentials: LoginRequest):
    normalized_email = credentials.email.strip().lower()
    session = _supabase_auth_request(
        "/auth/v1/token?grant_type=password",
        {"email": normalized_email, "password": credentials.password},
    )
    user = session.get("user") or {}
    if not session.get("access_token") or not user.get("id"):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    return {"token": session["access_token"], "user_id": user["id"], "email": user.get("email", normalized_email)}


@app.post("/api/registro/", status_code=201)
def register_user(user: UserCreate):
    email = user.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=400, detail="Ingresa un correo válido.")
    response = _supabase_auth_request(
        "/auth/v1/signup",
        {
            "email": email,
            "password": user.password,
            "data": {"first_name": user.first_name.strip(), "last_name": user.last_name.strip()},
        },
    )
    auth_user = response.get("user") or {}
    return {
        "token": response.get("access_token"),
        "user_id": auth_user.get("id"),
        "email": auth_user.get("email", email),
        "needs_email_confirmation": not bool(response.get("access_token")),
    }


@app.get("/api/usuarios/{user_id}/limite")
def get_daily_limit(user_id: str):
    row = _get_row("users", {"id": f"eq.{user_id}"}, "daily_limit_minutes")
    minutes = 360 if row is None or row.get("daily_limit_minutes") is None else int(row["daily_limit_minutes"])
    return {"user_id": user_id, "daily_limit_hours": minutes // 60}


@app.put("/api/usuarios/{user_id}/limite")
def set_daily_limit(user_id: str, value: int):
    if value < 1 or value > 16:
        raise HTTPException(status_code=400, detail="El límite diario debe estar entre 1 y 16 horas.")
    _update_row("users", {"id": f"eq.{user_id}"}, {"daily_limit_minutes": value * 60})
    return {"user_id": user_id, "daily_limit_hours": value}


@app.get("/api/health/")
def health_check():
    return {
        "status": "ok",
        "database": "connected",
        "message": "Data API de Supabase conectada",
        "events_count": len(_get_rows("events", select="id")),
        "subtasks_count": len(_get_rows("subtasks", select="id")),
    }


@app.get("/api/hoy/")
def today_summary():
    all_subtasks = _get_rows("subtasks", {"status": "neq.done"})
    today = date.today()
    groups: dict[str, list[dict[str, Any]]] = {"vencidas": [], "hoy": [], "proximas": []}
    for item in all_subtasks:
        target_day = _coerce_date(item.get("target_date"))
        if target_day is None or target_day > today:
            groups["proximas"].append(item)
        elif target_day == today:
            groups["hoy"].append(item)
        else:
            groups["vencidas"].append(item)
    for items in groups.values():
        items.sort(key=lambda item: (_coerce_date(item.get("target_date")) or date.max, item.get("estimated_minutes", 0)))
    return groups


@app.get("/api/eventos/", response_model=list[EventOut])
def list_eventos():
    return _get_rows("events", {"order": "event_date.asc"})


@app.get("/api/eventos/{event_id}", response_model=EventOut)
@app.get("/api/eventos/{event_id}/", response_model=EventOut)
def get_event(event_id: str):
    event = _get_row("events", {"id": f"eq.{event_id}"})
    if not event:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)
    return event


@app.get("/api/eventos/{event_id}/progreso")
def get_event_progress(event_id: str):
    if not _get_row("events", {"id": f"eq.{event_id}"}, "id"):
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)
    subtasks = _get_rows("subtasks", {"event_id": f"eq.{event_id}"}, "id,status")
    total = len(subtasks)
    done = sum(1 for item in subtasks if item.get("status") == "done")
    return {"event_id": event_id, "total": total, "done": done, "percent": 0 if total == 0 else round(done / total * 100, 2)}


@app.post("/api/eventos/", response_model=EventOut, status_code=201)
def create_evento(evento: EventCreate):
    return _insert_row("events", _create_event_payload(evento))


@app.patch("/api/eventos/{event_id}", response_model=EventOut)
def update_event(event_id: str, changes: EventUpdate):
    current = _get_row("events", {"id": f"eq.{event_id}"})
    if not current:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)
    data = changes.model_dump(exclude_unset=True)
    for field in ("name", "event_type"):
        if field in data:
            data[field] = (data[field] or "").strip()
            if not data[field]:
                raise HTTPException(status_code=400, detail=f"{field} es obligatorio.")
    updated = _update_row("events", {"id": f"eq.{event_id}"}, data) if data else None
    return updated or current


@app.delete("/api/eventos/{event_id}")
def delete_event(event_id: str):
    event = _get_row("events", {"id": f"eq.{event_id}"}, "id")
    if not event:
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)
    _delete_rows("subtasks", {"event_id": f"eq.{event_id}"})
    _delete_rows("events", {"id": f"eq.{event_id}"})
    return {"message": "Evento eliminado correctamente.", "event_id": event_id}


@app.post("/api/eventos/{event_id}/subtareas/", response_model=SubtaskOut, status_code=201)
def create_subtask(event_id: str, subtask: SubtaskCreate):
    if not _get_row("events", {"id": f"eq.{event_id}"}, "id"):
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)
    title = subtask.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="El título de la gestión logística es obligatorio.")
    payload = {
        "id": str(uuid.uuid4()),
        "event_id": event_id,
        "task_id": None,
        "title": title,
        "description": subtask.description,
        "target_date": subtask.target_date,
        "estimated_minutes": subtask.estimated_minutes,
        "status": _normalize_status(subtask.status),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return _insert_row("subtasks", payload)


@app.get("/api/eventos/{event_id}/subtareas/", response_model=list[SubtaskOut])
def list_subtasks(event_id: str):
    if not _get_row("events", {"id": f"eq.{event_id}"}, "id"):
        raise HTTPException(status_code=404, detail=EVENT_NOT_FOUND)
    return _get_rows("subtasks", {"event_id": f"eq.{event_id}", "order": "target_date.asc.nullsfirst"})


@app.patch("/api/eventos/{event_id}/subtareas/{subtask_id}", response_model=SubtaskOut)
def update_subtask(event_id: str, subtask_id: str, changes: SubtaskUpdate):
    current = _get_row("subtasks", {"id": f"eq.{subtask_id}", "event_id": f"eq.{event_id}"})
    if not current:
        raise HTTPException(status_code=404, detail="Subtarea no encontrada.")
    data = changes.model_dump(exclude_unset=True)
    if "title" in data:
        data["title"] = (data["title"] or "").strip()
        if not data["title"]:
            raise HTTPException(status_code=400, detail="El título es obligatorio.")
    if "estimated_minutes" in data and (data["estimated_minutes"] is None or data["estimated_minutes"] <= 0):
        raise HTTPException(status_code=400, detail="Los minutos estimados deben ser mayores que 0.")
    if "status" in data:
        data["status"] = _normalize_status(data["status"])
    updated = _update_row("subtasks", {"id": f"eq.{subtask_id}", "event_id": f"eq.{event_id}"}, data) if data else None
    return updated or current


@app.delete("/api/eventos/{event_id}/subtareas/{subtask_id}")
def delete_subtask(event_id: str, subtask_id: str):
    deleted = _delete_rows("subtasks", {"id": f"eq.{subtask_id}", "event_id": f"eq.{event_id}"})
    if not deleted:
        raise HTTPException(status_code=404, detail="Subtarea no encontrada.")
    return {"message": "Subtarea eliminada correctamente.", "subtask_id": subtask_id}
