# Sistema de Monitoreo PPPoE — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted web system that polls multiple Mikrotik routers every 5 minutes over their REST API, tracks per-client PPPoE traffic (current + accumulated, surviving session reconnects), resets accumulation on a configurable schedule, and presents a dashboard with a sortable heavy-user table, per-client charts, and threshold alerts.

**Architecture:** Monolithic FastAPI backend (REST API + in-process APScheduler for polling/reset/purge jobs) backed by PostgreSQL, paired with a React/Vite/TypeScript frontend served by Nginx. Three Docker Compose services: `db`, `backend`, `frontend`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x (sync), Alembic, APScheduler (BackgroundScheduler, thread-based), httpx (sync client), passlib[bcrypt], PyJWT, cryptography (Fernet), pytest. React 18 + TypeScript + Vite, Recharts, React Router, Nginx.

**Spec:** `docs/superpowers/specs/2026-09-22-pppoe-monitor-design.md`

## Global Constraints

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x sync engine (psycopg2), PostgreSQL 16, Alembic migrations, pytest.
- Scheduler: APScheduler `BackgroundScheduler` (thread pool), started on FastAPI startup, stopped on shutdown.
- Auth: JWT (PyJWT, HS256), passwords hashed with passlib bcrypt.
- Router API credentials encrypted at rest with Fernet (`cryptography` lib); master key from `MASTER_ENCRYPTION_KEY` env var, never committed.
- All traffic values stored as `BIGINT` bytes; unit conversion (KB/MB/GB) happens only in the frontend.
- Default settings: polling interval 300s, monthly reset on day 1 00:00, retention 90 days — all stored in the `settings` table and overridable via API.
- Docker Compose: exactly 3 services (`db`, `backend`, `frontend`). No Redis, no Celery.
- Backend tests live in `backend/tests/`, mirroring `backend/app/` structure, run with `pytest`.
- Frontend built with Vite; production build served as static files by Nginx, which proxies `/api/*` to `backend:8000`.

---

## File Structure

```
backend/
  app/
    main.py
    config.py
    database.py
    models/
      __init__.py
      user.py
      router.py
      client.py
      traffic.py
      alert.py
      settings.py
    schemas/
      __init__.py
      auth.py
      router.py
      client.py
      settings.py
      alert.py
    core/
      security.py
      crypto.py
    services/
      mikrotik_client.py
      delta.py
      polling.py
      scheduler.py
      notifications.py
      alerts.py
    api/
      deps.py
      auth.py
      routers.py
      clients.py
      dashboard.py
      settings.py
      alerts.py
  alembic/
    env.py
    versions/
  alembic.ini
  tests/
    conftest.py
    test_security.py
    test_crypto.py
    test_delta.py
    test_mikrotik_client.py
    test_polling.py
    test_api_auth.py
    test_api_routers.py
    test_api_clients.py
    test_api_settings.py
    test_api_alerts.py
  requirements.txt
  Dockerfile
frontend/
  src/
    api/client.ts
    context/AuthContext.tsx
    components/Layout.tsx
    components/ProtectedRoute.tsx
    pages/Login.tsx
    pages/Dashboard.tsx
    pages/Clients.tsx
    pages/ClientDetail.tsx
    pages/RoutersAdmin.tsx
    pages/SettingsAdmin.tsx
    App.tsx
    main.tsx
  Dockerfile
  nginx.conf
  package.json
  vite.config.ts
docker-compose.yml
.env.example
README.md
```

---

### Task 1: Backend project scaffolding + health check

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/app/main.py`
- Test: `backend/tests/test_main.py`
- Test: `backend/tests/conftest.py`

**Interfaces:**
- Produces: `app.config.settings` (a `Settings` instance with `DATABASE_URL`, `JWT_SECRET`, `MASTER_ENCRYPTION_KEY`, `TOKEN_EXPIRE_MINUTES`, all read from env vars via `pydantic-settings`), `app.main.app` (the FastAPI instance).

- [ ] **Step 1: Create `backend/requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy==2.0.35
psycopg2-binary==2.9.9
alembic==1.13.2
pydantic-settings==2.5.2
passlib[bcrypt]==1.7.4
pyjwt==2.9.0
cryptography==43.0.1
httpx==0.27.2
apscheduler==3.10.4
pytest==8.3.3
pytest-cov==5.0.0
```

- [ ] **Step 2: Write `backend/app/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql://pppoe:pppoe@localhost:5432/pppoe"
    JWT_SECRET: str = "changeme-in-env"
    JWT_ALGORITHM: str = "HS256"
    TOKEN_EXPIRE_MINUTES: int = 480
    MASTER_ENCRYPTION_KEY: str = ""


settings = Settings()
```

- [ ] **Step 3: Write `backend/app/main.py`**

```python
from fastapi import FastAPI

app = FastAPI(title="PPPoE Monitor")


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Write `backend/tests/conftest.py`**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

- [ ] **Step 5: Write the failing test `backend/tests/test_main.py`**

```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 6: Install deps and run test**

Run: `cd backend && pip install -r requirements.txt && pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/app/__init__.py backend/app/config.py backend/app/main.py backend/tests/conftest.py backend/tests/test_main.py
git commit -m "feat(backend): scaffold FastAPI app with health check"
```

---

### Task 2: Database connection + Alembic setup

**Files:**
- Create: `backend/app/database.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Test: `backend/tests/test_database.py`

**Interfaces:**
- Consumes: `app.config.settings.DATABASE_URL` (Task 1)
- Produces: `app.database.Base` (SQLAlchemy declarative base), `app.database.engine`, `app.database.SessionLocal`, `app.database.get_db()` (FastAPI dependency yielding a `Session`)

- [ ] **Step 1: Write `backend/app/database.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: Write the failing test `backend/tests/test_database.py`**

Requires a running Postgres reachable at `DATABASE_URL` (the `db` service from docker-compose, started separately for local dev — see Task 21). This test just verifies the engine can connect.

```python
from sqlalchemy import text

from app.database import engine


def test_can_connect_to_database():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
```

- [ ] **Step 3: Start a local Postgres for development**

Run: `docker run -d --name pppoe-dev-db -e POSTGRES_USER=pppoe -e POSTGRES_PASSWORD=pppoe -e POSTGRES_DB=pppoe -p 5432:5432 postgres:16`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_database.py -v`
Expected: PASS

- [ ] **Step 5: Initialize Alembic**

Run: `cd backend && alembic init alembic`

- [ ] **Step 6: Edit `backend/alembic.ini`**

Replace the `sqlalchemy.url` line with a placeholder (the real URL is injected in `env.py` from `app.config.settings`):

```ini
sqlalchemy.url = driver://user:pass@localhost/dbname
```

- [ ] **Step 7: Edit `backend/alembic/env.py`**

Add near the top (after existing imports) and modify `run_migrations_offline`/`run_migrations_online` to use `target_metadata`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import Base
import app.models  # noqa: F401 ensures all models are registered

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata
```

(`app.models` doesn't exist yet — it's created empty in this task and populated in Tasks 3, 5-8.)

- [ ] **Step 8: Create empty `backend/app/models/__init__.py`**

```python
# Models are imported here as they're created, so Alembic autogenerate
# and Base.metadata pick them up.
```

- [ ] **Step 9: Run test again to confirm nothing broke**

Run: `cd backend && pytest tests/test_database.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add backend/app/database.py backend/app/models/__init__.py backend/alembic.ini backend/alembic/ backend/tests/test_database.py
git commit -m "feat(backend): add SQLAlchemy engine/session and Alembic setup"
```

---

### Task 3: User model + password hashing + JWT auth

**Files:**
- Create: `backend/app/models/user.py`
- Create: `backend/app/core/security.py`
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/api/deps.py`
- Create: `backend/app/api/auth.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_security.py`
- Test: `backend/tests/test_api_auth.py`

**Interfaces:**
- Consumes: `app.database.Base`, `get_db` (Task 2)
- Produces: `app.models.user.User` (`id, username, password_hash, created_at`), `app.core.security.hash_password(plain) -> str`, `verify_password(plain, hashed) -> bool`, `create_access_token(subject: str) -> str`, `decode_access_token(token: str) -> str` (returns subject or raises `JWTError`), `app.api.deps.get_current_user` (FastAPI dependency, raises 401), router `app.api.auth.router` mounted at `/auth` with `POST /auth/login`.

- [ ] **Step 1: Write `backend/app/models/user.py`**

```python
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 2: Register it in `backend/app/models/__init__.py`**

```python
from app.models.user import User  # noqa: F401
```

- [ ] **Step 3: Write the failing test `backend/tests/test_security.py`**

```python
import pytest
from jwt import PyJWTError

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password():
    hashed = hash_password("s3cret")
    assert hashed != "s3cret"
    assert verify_password("s3cret", hashed)
    assert not verify_password("wrong", hashed)


def test_create_and_decode_access_token():
    token = create_access_token("admin")
    assert decode_access_token(token) == "admin"


def test_decode_invalid_token_raises():
    with pytest.raises(PyJWTError):
        decode_access_token("not-a-real-token")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && pytest tests/test_security.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.security'`

- [ ] **Step 5: Write `backend/app/core/security.py`**

```python
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    return payload["sub"]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest tests/test_security.py -v`
Expected: PASS

- [ ] **Step 7: Write `backend/app/schemas/auth.py`**

```python
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
```

- [ ] **Step 8: Write `backend/app/api/deps.py`**

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User

_bearer = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    try:
        username = decode_access_token(credentials.credentials)
    except PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user
```

- [ ] **Step 9: Write `backend/app/api/auth.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(user.username))
```

- [ ] **Step 10: Wire the router into `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.api.auth import router as auth_router

app = FastAPI(title="PPPoE Monitor")
app.include_router(auth_router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 11: Generate and apply the Alembic migration**

Run:
```bash
cd backend
alembic revision --autogenerate -m "add users table"
alembic upgrade head
```

- [ ] **Step 12: Write the failing test `backend/tests/test_api_auth.py`**

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.database import SessionLocal
from app.main import app
from app.models.user import User

client = TestClient(app)


def _make_user(db: Session, username: str, password: str) -> User:
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    return user


def test_login_with_valid_credentials_returns_token():
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "testadmin").delete()
        db.commit()
        _make_user(db, "testadmin", "s3cret")
    finally:
        db.close()

    response = client.post("/auth/login", json={"username": "testadmin", "password": "s3cret"})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_with_invalid_credentials_returns_401():
    response = client.post("/auth/login", json={"username": "nope", "password": "wrong"})
    assert response.status_code == 401
```

- [ ] **Step 13: Run test to verify it passes**

Run: `cd backend && pytest tests/test_api_auth.py -v`
Expected: PASS

- [ ] **Step 14: Commit**

```bash
git add backend/app backend/alembic/versions backend/tests/test_security.py backend/tests/test_api_auth.py
git commit -m "feat(backend): add user model, JWT auth, and login endpoint"
```

---

### Task 4: Encryption helper for router API passwords

**Files:**
- Create: `backend/app/core/crypto.py`
- Test: `backend/tests/test_crypto.py`

**Interfaces:**
- Consumes: `app.config.settings.MASTER_ENCRYPTION_KEY`
- Produces: `app.core.crypto.encrypt(plain: str) -> str`, `app.core.crypto.decrypt(token: str) -> str`

- [ ] **Step 1: Write the failing test `backend/tests/test_crypto.py`**

```python
from app.core.crypto import decrypt, encrypt


def test_encrypt_then_decrypt_roundtrips():
    plain = "super-secret-router-password"
    token = encrypt(plain)
    assert token != plain
    assert decrypt(token) == plain
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Generate a dev master key and add it to `backend/.env`**

Run: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

Add the output to `backend/.env` (create the file if missing) as:
```
MASTER_ENCRYPTION_KEY=<generated-key>
```

- [ ] **Step 4: Write `backend/app/core/crypto.py`**

```python
from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.MASTER_ENCRYPTION_KEY.encode())


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_crypto.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/crypto.py backend/tests/test_crypto.py
git commit -m "feat(backend): add Fernet-based encryption for router credentials"
```

---

### Task 5: Router model + CRUD endpoints

**Files:**
- Create: `backend/app/models/router.py`
- Create: `backend/app/schemas/router.py`
- Create: `backend/app/api/routers.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_routers.py`

**Interfaces:**
- Consumes: `Base`, `get_db` (Task 2); `encrypt`/`decrypt` (Task 4); `get_current_user` (Task 3)
- Produces: `app.models.router.Router` (`id, name, host, port, api_username, api_password_encrypted, use_tls, verify_tls, enabled, created_at`), router `app.api.routers.router` mounted at `/routers` with full CRUD, all endpoints requiring auth.

- [ ] **Step 1: Write `backend/app/models/router.py`**

```python
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Router(Base):
    __tablename__ = "routers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=443)
    api_username: Mapped[str] = mapped_column(String(128))
    api_password_encrypted: Mapped[str] = mapped_column(String(512))
    use_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 2: Register in `backend/app/models/__init__.py`**

```python
from app.models.router import Router  # noqa: F401
from app.models.user import User  # noqa: F401
```

- [ ] **Step 3: Write `backend/app/schemas/router.py`**

```python
from datetime import datetime

from pydantic import BaseModel


class RouterCreate(BaseModel):
    name: str
    host: str
    port: int = 443
    api_username: str
    api_password: str
    use_tls: bool = True
    verify_tls: bool = False
    enabled: bool = True


class RouterUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    api_username: str | None = None
    api_password: str | None = None
    use_tls: bool | None = None
    verify_tls: bool | None = None
    enabled: bool | None = None


class RouterOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    api_username: str
    use_tls: bool
    verify_tls: bool
    enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}
```

Note: `RouterOut` deliberately omits the password/encrypted field — it's never returned to the frontend.

- [ ] **Step 4: Write the failing test `backend/tests/test_api_routers.py`**

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.router import Router
from app.models.user import User

client = TestClient(app)


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "router-tester").delete()
        db.commit()
        db.add(User(username="router-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    token = create_access_token("router-tester")
    return {"Authorization": f"Bearer {token}"}


def test_create_and_list_router():
    headers = _auth_headers()
    payload = {
        "name": "Router Centro",
        "host": "10.0.0.1",
        "api_username": "admin",
        "api_password": "secret123",
    }
    create_resp = client.post("/routers", json=payload, headers=headers)
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["name"] == "Router Centro"
    assert "api_password" not in body
    assert "api_password_encrypted" not in body

    list_resp = client.get("/routers", headers=headers)
    assert list_resp.status_code == 200
    assert any(r["name"] == "Router Centro" for r in list_resp.json())


def test_router_endpoints_require_auth():
    response = client.get("/routers")
    assert response.status_code in (401, 403)
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd backend && pytest tests/test_api_routers.py -v`
Expected: FAIL (404, router not mounted)

- [ ] **Step 6: Write `backend/app/api/routers.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.crypto import encrypt
from app.database import get_db
from app.models.router import Router
from app.schemas.router import RouterCreate, RouterOut, RouterUpdate

router = APIRouter(prefix="/routers", tags=["routers"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[RouterOut])
def list_routers(db: Session = Depends(get_db)):
    return db.query(Router).order_by(Router.name).all()


@router.post("", response_model=RouterOut, status_code=status.HTTP_201_CREATED)
def create_router(payload: RouterCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"api_password"})
    obj = Router(**data, api_password_encrypted=encrypt(payload.api_password))
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/{router_id}", response_model=RouterOut)
def update_router(router_id: int, payload: RouterUpdate, db: Session = Depends(get_db)):
    obj = db.get(Router, router_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Router not found")
    data = payload.model_dump(exclude_unset=True, exclude={"api_password"})
    for key, value in data.items():
        setattr(obj, key, value)
    if payload.api_password is not None:
        obj.api_password_encrypted = encrypt(payload.api_password)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{router_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_router(router_id: int, db: Session = Depends(get_db)):
    obj = db.get(Router, router_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Router not found")
    db.delete(obj)
    db.commit()
```

- [ ] **Step 7: Wire router into `backend/app/main.py`**

```python
from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.routers import router as routers_router

app = FastAPI(title="PPPoE Monitor")
app.include_router(auth_router)
app.include_router(routers_router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 8: Generate and apply migration**

Run:
```bash
cd backend
alembic revision --autogenerate -m "add routers table"
alembic upgrade head
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_api_routers.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add backend/app backend/alembic/versions backend/tests/test_api_routers.py
git commit -m "feat(backend): add router model and CRUD endpoints"
```

---

### Task 6: PPPoE client & session_state models

**Files:**
- Create: `backend/app/models/client.py`
- Modify: `backend/app/models/__init__.py`

**Interfaces:**
- Consumes: `Base` (Task 2), `Router` (Task 5)
- Produces: `app.models.client.PPPoEClient` (`id, router_id, username, first_seen, last_seen, is_active`, unique `(router_id, username)`), `app.models.client.SessionState` (`client_id (PK, FK)`, `last_uptime_seconds, last_rx_bytes, last_tx_bytes, last_poll_at`)

- [ ] **Step 1: Write `backend/app/models/client.py`**

```python
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PPPoEClient(Base):
    __tablename__ = "pppoe_clients"
    __table_args__ = (UniqueConstraint("router_id", "username", name="uq_client_router_username"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    router_id: Mapped[int] = mapped_column(ForeignKey("routers.id", ondelete="CASCADE"))
    username: Mapped[str] = mapped_column(String(128), index=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SessionState(Base):
    __tablename__ = "session_state"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("pppoe_clients.id", ondelete="CASCADE"), primary_key=True
    )
    last_uptime_seconds: Mapped[int] = mapped_column(Integer, default=0)
    last_rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    last_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 2: Register in `backend/app/models/__init__.py`**

```python
from app.models.client import PPPoEClient, SessionState  # noqa: F401
from app.models.router import Router  # noqa: F401
from app.models.user import User  # noqa: F401
```

- [ ] **Step 3: Generate and apply migration**

Run:
```bash
cd backend
alembic revision --autogenerate -m "add pppoe_clients and session_state tables"
alembic upgrade head
```

- [ ] **Step 4: Verify migration applied cleanly**

Run: `cd backend && alembic current`
Expected: shows the new revision as current head, no errors.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models backend/alembic/versions
git commit -m "feat(backend): add PPPoEClient and SessionState models"
```

---

### Task 7: Traffic sample & accumulation period models

**Files:**
- Create: `backend/app/models/traffic.py`
- Modify: `backend/app/models/__init__.py`

**Interfaces:**
- Consumes: `Base` (Task 2), `PPPoEClient` (Task 6)
- Produces: `app.models.traffic.TrafficSample` (`id, client_id, sampled_at, rx_bytes_delta, tx_bytes_delta, rx_bps, tx_bps, is_online`), `app.models.traffic.AccumulationPeriod` (`id, client_id, period_start, period_end (nullable), rx_bytes_total, tx_bytes_total`)

- [ ] **Step 1: Write `backend/app/models/traffic.py`**

```python
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TrafficSample(Base):
    __tablename__ = "traffic_samples"
    __table_args__ = (Index("ix_traffic_samples_client_time", "client_id", "sampled_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("pppoe_clients.id", ondelete="CASCADE"))
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    rx_bytes_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    rx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    is_online: Mapped[bool] = mapped_column(Boolean, default=True)


class AccumulationPeriod(Base):
    __tablename__ = "accumulation_periods"
    __table_args__ = (Index("ix_accum_client_active", "client_id", "period_end"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("pppoe_clients.id", ondelete="CASCADE"))
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rx_bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)
    tx_bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)
```

- [ ] **Step 2: Register in `backend/app/models/__init__.py`**

```python
from app.models.client import PPPoEClient, SessionState  # noqa: F401
from app.models.router import Router  # noqa: F401
from app.models.traffic import AccumulationPeriod, TrafficSample  # noqa: F401
from app.models.user import User  # noqa: F401
```

- [ ] **Step 3: Generate and apply migration**

Run:
```bash
cd backend
alembic revision --autogenerate -m "add traffic_samples and accumulation_periods tables"
alembic upgrade head
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/models backend/alembic/versions
git commit -m "feat(backend): add TrafficSample and AccumulationPeriod models"
```

---

### Task 8: Alert & Settings models

**Files:**
- Create: `backend/app/models/alert.py`
- Create: `backend/app/models/settings.py`
- Modify: `backend/app/models/__init__.py`

**Interfaces:**
- Consumes: `Base` (Task 2), `PPPoEClient` (Task 6)
- Produces: `app.models.alert.AlertThreshold` (`id, client_id (nullable), bytes_threshold, notify_channel`), `app.models.alert.AlertEvent` (`id, client_id, threshold_id, triggered_at, accumulated_bytes_at_trigger`), `app.models.settings.AppSetting` (`key (PK, str), value (str)`) — a simple key/value table.

- [ ] **Step 1: Write `backend/app/models/alert.py`**

```python
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AlertThreshold(Base):
    __tablename__ = "alert_thresholds"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("pppoe_clients.id", ondelete="CASCADE"), nullable=True
    )
    bytes_threshold: Mapped[int] = mapped_column(BigInteger)
    notify_channel: Mapped[str] = mapped_column(String(32), default="email")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("pppoe_clients.id", ondelete="CASCADE"))
    threshold_id: Mapped[int] = mapped_column(ForeignKey("alert_thresholds.id", ondelete="CASCADE"))
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    accumulated_bytes_at_trigger: Mapped[int] = mapped_column(BigInteger)
```

- [ ] **Step 2: Write `backend/app/models/settings.py`**

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(512))
```

Known keys used by the system (seeded in Task 9's migration data step): `polling_interval_seconds` (default `"300"`), `reset_day_of_month` (default `"1"`), `retention_days` (default `"90"`), `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password`, `smtp_from`, `smtp_to`, `telegram_bot_token`, `telegram_chat_id`.

- [ ] **Step 3: Register in `backend/app/models/__init__.py`**

```python
from app.models.alert import AlertEvent, AlertThreshold  # noqa: F401
from app.models.client import PPPoEClient, SessionState  # noqa: F401
from app.models.router import Router  # noqa: F401
from app.models.settings import AppSetting  # noqa: F401
from app.models.traffic import AccumulationPeriod, TrafficSample  # noqa: F401
from app.models.user import User  # noqa: F401
```

- [ ] **Step 4: Generate and apply migration, seeding default settings**

Run: `cd backend && alembic revision --autogenerate -m "add alert and settings tables"`

Edit the generated migration file's `upgrade()` function to append default rows after the `create_table` calls:

```python
from sqlalchemy import table, column, String
from sqlalchemy.sql import insert

settings_table = table("settings", column("key", String), column("value", String))

op.bulk_insert(
    settings_table,
    [
        {"key": "polling_interval_seconds", "value": "300"},
        {"key": "reset_day_of_month", "value": "1"},
        {"key": "retention_days", "value": "90"},
    ],
)
```

Run: `cd backend && alembic upgrade head`

- [ ] **Step 5: Verify seed data**

Run: `cd backend && python3 -c "
from app.database import SessionLocal
from app.models.settings import AppSetting
db = SessionLocal()
rows = db.query(AppSetting).all()
assert len(rows) == 3
print([r.key for r in rows])
"`
Expected: prints the three keys, no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models backend/alembic/versions
git commit -m "feat(backend): add alert threshold/event and settings models"
```

---

### Task 9: Delta calculation (pure logic, TDD-heavy)

**Files:**
- Create: `backend/app/services/delta.py`
- Test: `backend/tests/test_delta.py`

**Interfaces:**
- Produces: `app.services.delta.compute_delta(current_uptime: int, current_bytes: int, last_uptime: int | None, last_bytes: int | None) -> int`

- [ ] **Step 1: Write the failing tests `backend/tests/test_delta.py`**

```python
from app.services.delta import compute_delta


def test_first_seen_returns_current_bytes_as_delta():
    assert compute_delta(current_uptime=120, current_bytes=5000, last_uptime=None, last_bytes=None) == 5000


def test_same_session_continuing_returns_difference():
    assert compute_delta(current_uptime=600, current_bytes=9000, last_uptime=300, last_bytes=5000) == 4000


def test_session_restarted_returns_current_bytes_as_delta():
    # uptime dropped => interface/session was recreated, counters reset to 0
    assert compute_delta(current_uptime=30, current_bytes=1200, last_uptime=900, last_bytes=50000) == 1200


def test_same_uptime_no_traffic_returns_zero():
    assert compute_delta(current_uptime=300, current_bytes=5000, last_uptime=300, last_bytes=5000) == 0


def test_never_returns_negative_delta():
    # Defensive: if bytes somehow decreased without an uptime drop, clamp to 0
    assert compute_delta(current_uptime=610, current_bytes=4000, last_uptime=600, last_bytes=5000) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_delta.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `backend/app/services/delta.py`**

```python
def compute_delta(
    current_uptime: int,
    current_bytes: int,
    last_uptime: int | None,
    last_bytes: int | None,
) -> int:
    """Compute bytes transferred since the last poll for a single counter
    (rx or tx), handling PPPoE session restarts.

    Mikrotik resets the interface byte counter to 0 whenever a PPPoE
    session reconnects (a new dynamic interface is created). We detect
    that by uptime going backwards relative to the last poll.
    """
    if last_uptime is None or last_bytes is None:
        return current_bytes
    if current_uptime < last_uptime:
        return current_bytes
    return max(current_bytes - last_bytes, 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_delta.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/delta.py backend/tests/test_delta.py
git commit -m "feat(backend): add pure delta calculation for PPPoE traffic counters"
```

---

### Task 10: Mikrotik REST API client service

**Files:**
- Create: `backend/app/services/mikrotik_client.py`
- Test: `backend/tests/test_mikrotik_client.py`

**Interfaces:**
- Consumes: `app.models.router.Router` (Task 5), `decrypt` (Task 4)
- Produces: `app.services.mikrotik_client.MikrotikSession` (a `NamedTuple`/dataclass: `username: str, uptime_seconds: int, rx_bytes: int, tx_bytes: int`), `app.services.mikrotik_client.fetch_active_sessions(router: Router) -> list[MikrotikSession]` (raises `MikrotikError` on any HTTP/connection failure)

**Note on RouterOS specifics:** `GET /rest/ppp/active` returns each active PPP session with `name` (the PPPoE username) and `uptime` (a duration string like `"1d02:03:04"` or `"02:03:04"`). Cumulative byte counters live on the dynamic interface RouterOS creates per session, fetched via `GET /rest/interface` and matched by `name == "<pppoe-username>"` (RouterOS's default naming for PPPoE-server dynamic interfaces). This matching convention should be verified against the user's actual routers once SSH/router access exists — if their interface naming differs, `_match_interface_name` is the single function to adjust.

- [ ] **Step 1: Write the failing tests `backend/tests/test_mikrotik_client.py`**

```python
import httpx
import pytest

from app.models.router import Router
from app.services.mikrotik_client import MikrotikError, fetch_active_sessions, parse_uptime


def _make_router() -> Router:
    return Router(
        id=1,
        name="Test Router",
        host="10.0.0.1",
        port=443,
        api_username="admin",
        api_password_encrypted="",
        use_tls=True,
        verify_tls=False,
        enabled=True,
    )


def test_parse_uptime_handles_days_hours_minutes_seconds():
    assert parse_uptime("1d02:03:04") == 1 * 86400 + 2 * 3600 + 3 * 60 + 4


def test_parse_uptime_handles_hours_minutes_seconds_only():
    assert parse_uptime("02:03:04") == 2 * 3600 + 3 * 60 + 4


def test_fetch_active_sessions_combines_ppp_and_interface_data(monkeypatch):
    router = _make_router()

    def fake_get(self, url, **kwargs):
        if url.endswith("/rest/ppp/active"):
            payload = [{"name": "cliente1", "uptime": "00:10:00"}]
        elif url.endswith("/rest/interface"):
            payload = [{"name": "cliente1", "rx-byte": "1000", "tx-byte": "2000"}]
        else:
            raise AssertionError(f"unexpected url {url}")
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    sessions = fetch_active_sessions(router)

    assert len(sessions) == 1
    assert sessions[0].username == "cliente1"
    assert sessions[0].uptime_seconds == 600
    assert sessions[0].rx_bytes == 1000
    assert sessions[0].tx_bytes == 2000


def test_fetch_active_sessions_raises_mikrotik_error_on_http_failure(monkeypatch):
    router = _make_router()

    def fake_get(self, url, **kwargs):
        raise httpx.ConnectTimeout("timed out", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    with pytest.raises(MikrotikError):
        fetch_active_sessions(router)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_mikrotik_client.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `backend/app/services/mikrotik_client.py`**

```python
import re
from dataclasses import dataclass

import httpx

from app.core.crypto import decrypt
from app.models.router import Router

_UPTIME_RE = re.compile(
    r"(?:(?P<days>\d+)d)?(?:(?P<hours>\d+):)?(?P<minutes>\d+):(?P<seconds>\d+)"
)


class MikrotikError(RuntimeError):
    pass


@dataclass(frozen=True)
class MikrotikSession:
    username: str
    uptime_seconds: int
    rx_bytes: int
    tx_bytes: int


def parse_uptime(raw: str) -> int:
    match = _UPTIME_RE.fullmatch(raw.strip())
    if not match:
        raise MikrotikError(f"Unrecognized uptime format: {raw!r}")
    parts = match.groupdict(default="0")
    return (
        int(parts["days"]) * 86400
        + int(parts["hours"]) * 3600
        + int(parts["minutes"]) * 60
        + int(parts["seconds"])
    )


def _client_for(router: Router) -> httpx.Client:
    scheme = "https" if router.use_tls else "http"
    base_url = f"{scheme}://{router.host}:{router.port}"
    password = decrypt(router.api_password_encrypted) if router.api_password_encrypted else ""
    return httpx.Client(
        base_url=base_url,
        auth=(router.api_username, password),
        verify=router.verify_tls,
        timeout=10.0,
    )


def _match_interface_name(username: str) -> str:
    """RouterOS's default dynamic interface name for a PPPoE-server session
    is the client's username. Adjust here if the user's routers use a
    different naming convention (verify against a real router)."""
    return username


def fetch_active_sessions(router: Router) -> list[MikrotikSession]:
    try:
        with _client_for(router) as client:
            ppp_response = client.get("/rest/ppp/active")
            ppp_response.raise_for_status()
            interface_response = client.get("/rest/interface")
            interface_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise MikrotikError(f"Failed to reach router {router.name}: {exc}") from exc

    interfaces_by_name = {row["name"]: row for row in interface_response.json()}

    sessions = []
    for entry in ppp_response.json():
        username = entry["name"]
        iface = interfaces_by_name.get(_match_interface_name(username), {})
        sessions.append(
            MikrotikSession(
                username=username,
                uptime_seconds=parse_uptime(entry["uptime"]),
                rx_bytes=int(iface.get("rx-byte", 0)),
                tx_bytes=int(iface.get("tx-byte", 0)),
            )
        )
    return sessions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_mikrotik_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mikrotik_client.py backend/tests/test_mikrotik_client.py
git commit -m "feat(backend): add Mikrotik REST API client for active PPPoE sessions"
```

---

### Task 11: Polling orchestration service

**Files:**
- Create: `backend/app/services/polling.py`
- Test: `backend/tests/test_polling.py`

**Interfaces:**
- Consumes: `fetch_active_sessions`, `MikrotikSession`, `MikrotikError` (Task 10); `compute_delta` (Task 9); `PPPoEClient`, `SessionState` (Task 6); `TrafficSample`, `AccumulationPeriod` (Task 7); `Router` (Task 5); `SessionLocal` (Task 2)
- Produces: `app.services.polling.poll_router(db: Session, router: Router) -> None` (polls one router, upserts clients, writes samples/accumulation; catches and logs `MikrotikError` without raising), `app.services.polling.poll_all_routers() -> None` (opens its own session, fetches all enabled routers, calls `poll_router` for each — errors in one router don't stop the others)

- [ ] **Step 1: Write the failing tests `backend/tests/test_polling.py`**

```python
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient, SessionState
from app.models.router import Router
from app.models.traffic import AccumulationPeriod, TrafficSample
from app.services.mikrotik_client import MikrotikError, MikrotikSession
from app.services.polling import poll_router


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def router(db: Session):
    obj = Router(
        name="Poll Test Router",
        host="10.0.0.9",
        api_username="admin",
        api_password_encrypted="",
    )
    db.add(obj)
    db.commit()
    yield obj
    db.query(TrafficSample).filter(
        TrafficSample.client_id.in_(
            db.query(PPPoEClient.id).filter(PPPoEClient.router_id == obj.id)
        )
    ).delete(synchronize_session=False)
    db.query(AccumulationPeriod).filter(
        AccumulationPeriod.client_id.in_(
            db.query(PPPoEClient.id).filter(PPPoEClient.router_id == obj.id)
        )
    ).delete(synchronize_session=False)
    db.query(SessionState).filter(
        SessionState.client_id.in_(
            db.query(PPPoEClient.id).filter(PPPoEClient.router_id == obj.id)
        )
    ).delete(synchronize_session=False)
    db.query(PPPoEClient).filter(PPPoEClient.router_id == obj.id).delete()
    db.delete(obj)
    db.commit()


def test_poll_router_creates_client_and_first_sample(db, router, monkeypatch):
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [MikrotikSession(username="nuevo_cliente", uptime_seconds=300, rx_bytes=1000, tx_bytes=500)],
    )

    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="nuevo_cliente").first()
    assert client is not None
    assert client.is_active is True

    sample = db.query(TrafficSample).filter_by(client_id=client.id).first()
    assert sample.rx_bytes_delta == 1000
    assert sample.tx_bytes_delta == 500

    period = db.query(AccumulationPeriod).filter_by(client_id=client.id, period_end=None).first()
    assert period.rx_bytes_total == 1000
    assert period.tx_bytes_total == 500


def test_poll_router_accumulates_across_polls(db, router, monkeypatch):
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [MikrotikSession(username="c2", uptime_seconds=300, rx_bytes=1000, tx_bytes=500)],
    )
    poll_router(db, router)

    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [MikrotikSession(username="c2", uptime_seconds=600, rx_bytes=1800, tx_bytes=900)],
    )
    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="c2").first()
    period = db.query(AccumulationPeriod).filter_by(client_id=client.id, period_end=None).first()
    assert period.rx_bytes_total == 1800  # 1000 + (1800-1000)
    assert period.tx_bytes_total == 900   # 500 + (900-500)


def test_poll_router_marks_missing_clients_inactive(db, router, monkeypatch):
    monkeypatch.setattr(
        "app.services.polling.fetch_active_sessions",
        lambda r: [MikrotikSession(username="c3", uptime_seconds=300, rx_bytes=1000, tx_bytes=500)],
    )
    poll_router(db, router)

    monkeypatch.setattr("app.services.polling.fetch_active_sessions", lambda r: [])
    poll_router(db, router)

    client = db.query(PPPoEClient).filter_by(router_id=router.id, username="c3").first()
    assert client.is_active is False


def test_poll_router_swallows_mikrotik_errors(db, router, monkeypatch):
    def raise_error(r):
        raise MikrotikError("router unreachable")

    monkeypatch.setattr("app.services.polling.fetch_active_sessions", raise_error)

    poll_router(db, router)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_polling.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `backend/app/services/polling.py`**

```python
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient, SessionState
from app.models.router import Router
from app.models.traffic import AccumulationPeriod, TrafficSample
from app.services.delta import compute_delta
from app.services.mikrotik_client import MikrotikError, fetch_active_sessions

logger = logging.getLogger(__name__)


def _get_or_create_client(db: Session, router_id: int, username: str) -> PPPoEClient:
    client = (
        db.query(PPPoEClient)
        .filter_by(router_id=router_id, username=username)
        .first()
    )
    now = datetime.now(timezone.utc)
    if client is None:
        client = PPPoEClient(router_id=router_id, username=username, first_seen=now, last_seen=now, is_active=True)
        db.add(client)
        db.flush()
    else:
        client.last_seen = now
        client.is_active = True
    return client


def _get_or_create_active_period(db: Session, client_id: int) -> AccumulationPeriod:
    period = (
        db.query(AccumulationPeriod)
        .filter_by(client_id=client_id, period_end=None)
        .first()
    )
    if period is None:
        period = AccumulationPeriod(client_id=client_id, period_start=datetime.now(timezone.utc))
        db.add(period)
        db.flush()
    return period


def poll_router(db: Session, router: Router) -> None:
    try:
        sessions = fetch_active_sessions(router)
    except MikrotikError:
        logger.exception("Polling failed for router %s (%s)", router.name, router.host)
        return

    now = datetime.now(timezone.utc)
    seen_client_ids: set[int] = set()

    for session in sessions:
        client = _get_or_create_client(db, router.id, session.username)
        seen_client_ids.add(client.id)

        state = db.get(SessionState, client.id)
        rx_delta = compute_delta(
            session.uptime_seconds,
            session.rx_bytes,
            state.last_uptime_seconds if state else None,
            state.last_rx_bytes if state else None,
        )
        tx_delta = compute_delta(
            session.uptime_seconds,
            session.tx_bytes,
            state.last_uptime_seconds if state else None,
            state.last_tx_bytes if state else None,
        )

        db.add(
            TrafficSample(
                client_id=client.id,
                sampled_at=now,
                rx_bytes_delta=rx_delta,
                tx_bytes_delta=tx_delta,
                rx_bps=0,
                tx_bps=0,
                is_online=True,
            )
        )

        period = _get_or_create_active_period(db, client.id)
        period.rx_bytes_total += rx_delta
        period.tx_bytes_total += tx_delta

        if state is None:
            state = SessionState(client_id=client.id)
            db.add(state)
        state.last_uptime_seconds = session.uptime_seconds
        state.last_rx_bytes = session.rx_bytes
        state.last_tx_bytes = session.tx_bytes
        state.last_poll_at = now

    stale_clients = (
        db.query(PPPoEClient)
        .filter(PPPoEClient.router_id == router.id, PPPoEClient.is_active.is_(True))
        .filter(~PPPoEClient.id.in_(seen_client_ids) if seen_client_ids else True)
        .all()
    )
    for client in stale_clients:
        client.is_active = False

    db.commit()


def poll_all_routers() -> None:
    db = SessionLocal()
    try:
        routers = db.query(Router).filter_by(enabled=True).all()
        for router in routers:
            poll_router(db, router)
    finally:
        db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_polling.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/polling.py backend/tests/test_polling.py
git commit -m "feat(backend): add polling orchestration with delta accumulation"
```

---

### Task 12: APScheduler wiring for the polling job

**Files:**
- Create: `backend/app/services/scheduler.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `poll_all_routers` (Task 11), `SessionLocal`, `AppSetting` (Task 8)
- Produces: `app.services.scheduler.start_scheduler() -> BackgroundScheduler`, `app.services.scheduler.stop_scheduler(scheduler) -> None`, `app.services.scheduler.get_polling_interval_seconds(db) -> int`

- [ ] **Step 1: Write the failing test `backend/tests/test_scheduler.py`**

```python
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.services.scheduler import get_polling_interval_seconds, start_scheduler, stop_scheduler


def test_get_polling_interval_seconds_reads_setting():
    db: Session = SessionLocal()
    try:
        row = db.get(AppSetting, "polling_interval_seconds")
        original = row.value
        row.value = "120"
        db.commit()

        assert get_polling_interval_seconds(db) == 120
    finally:
        row.value = original
        db.commit()
        db.close()


def test_start_and_stop_scheduler_registers_polling_job():
    scheduler = start_scheduler()
    try:
        job_ids = [job.id for job in scheduler.get_jobs()]
        assert "poll_all_routers" in job_ids
    finally:
        stop_scheduler(scheduler)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `backend/app/services/scheduler.py`**

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.services.polling import poll_all_routers


def get_polling_interval_seconds(db: Session) -> int:
    row = db.get(AppSetting, "polling_interval_seconds")
    return int(row.value) if row else 300


def start_scheduler() -> BackgroundScheduler:
    db = SessionLocal()
    try:
        interval = get_polling_interval_seconds(db)
    finally:
        db.close()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        poll_all_routers,
        trigger=IntervalTrigger(seconds=interval),
        id="poll_all_routers",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    return scheduler


def stop_scheduler(scheduler: BackgroundScheduler) -> None:
    scheduler.shutdown(wait=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Wire startup/shutdown into `backend/app/main.py`**

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.routers import router as routers_router
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = start_scheduler()
    app.state.scheduler = scheduler
    yield
    stop_scheduler(scheduler)


app = FastAPI(title="PPPoE Monitor", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(routers_router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 6: Manually verify the app starts cleanly**

Run: `cd backend && uvicorn app.main:app --reload &` then `curl localhost:8000/health`, then stop the server.
Expected: `{"status":"ok"}`, no startup errors in logs.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/scheduler.py backend/app/main.py backend/tests/test_scheduler.py
git commit -m "feat(backend): wire APScheduler polling job into app lifecycle"
```

---

### Task 13: Reset job (periodic accumulation reset)

**Files:**
- Create: `backend/app/services/reset.py`
- Modify: `backend/app/services/scheduler.py`
- Test: `backend/tests/test_reset.py`

**Interfaces:**
- Consumes: `AccumulationPeriod` (Task 7), `PPPoEClient` (Task 6), `AppSetting` (Task 8)
- Produces: `app.services.reset.run_reset_if_due(db: Session, today: date | None = None) -> None` (checks `reset_day_of_month`; if `today.day` matches and no period was already closed today, closes all open periods and opens new ones)

- [ ] **Step 1: Write the failing tests `backend/tests/test_reset.py`**

```python
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.settings import AppSetting
from app.models.traffic import AccumulationPeriod
from app.services.reset import run_reset_if_due


def _setup_client(db: Session) -> PPPoEClient:
    router = Router(name="Reset Test", host="10.0.0.5", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    client = PPPoEClient(router_id=router.id, username="reset_user")
    db.add(client)
    db.flush()
    return client


def test_run_reset_closes_and_reopens_active_period_on_reset_day():
    db = SessionLocal()
    try:
        setting = db.get(AppSetting, "reset_day_of_month")
        original = setting.value
        setting.value = "1"
        db.commit()

        client = _setup_client(db)
        period = AccumulationPeriod(client_id=client.id, rx_bytes_total=5000, tx_bytes_total=3000)
        db.add(period)
        db.commit()

        run_reset_if_due(db, today=date(2026, 10, 1))

        db.refresh(period)
        assert period.period_end is not None

        new_period = (
            db.query(AccumulationPeriod)
            .filter_by(client_id=client.id, period_end=None)
            .first()
        )
        assert new_period is not None
        assert new_period.rx_bytes_total == 0
        assert new_period.tx_bytes_total == 0
    finally:
        setting.value = original
        db.commit()
        db.rollback()
        db.close()


def test_run_reset_does_nothing_on_non_reset_day():
    db = SessionLocal()
    try:
        setting = db.get(AppSetting, "reset_day_of_month")
        original = setting.value
        setting.value = "1"
        db.commit()

        client = _setup_client(db)
        period = AccumulationPeriod(client_id=client.id, rx_bytes_total=5000, tx_bytes_total=3000)
        db.add(period)
        db.commit()

        run_reset_if_due(db, today=date(2026, 10, 15))

        db.refresh(period)
        assert period.period_end is None
    finally:
        setting.value = original
        db.commit()
        db.rollback()
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_reset.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `backend/app/services/reset.py`**

```python
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.models.traffic import AccumulationPeriod


def run_reset_if_due(db: Session, today: date | None = None) -> None:
    today = today or datetime.now(timezone.utc).date()
    setting = db.get(AppSetting, "reset_day_of_month")
    reset_day = int(setting.value) if setting else 1

    if today.day != reset_day:
        return

    now = datetime.now(timezone.utc)
    open_periods = db.query(AccumulationPeriod).filter_by(period_end=None).all()
    for period in open_periods:
        period.period_end = now
        db.add(
            AccumulationPeriod(
                client_id=period.client_id,
                period_start=now,
                period_end=None,
                rx_bytes_total=0,
                tx_bytes_total=0,
            )
        )
    db.commit()


def run_reset_job() -> None:
    db = SessionLocal()
    try:
        run_reset_if_due(db)
    finally:
        db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_reset.py -v`
Expected: PASS

- [ ] **Step 5: Register the daily reset-check job in `backend/app/services/scheduler.py`**

Add the import and a second `add_job` call inside `start_scheduler`:

```python
from apscheduler.triggers.cron import CronTrigger

from app.services.reset import run_reset_job

# inside start_scheduler(), after the polling job's add_job call:
scheduler.add_job(
    run_reset_job,
    trigger=CronTrigger(hour=0, minute=5),
    id="run_reset_job",
    replace_existing=True,
    max_instances=1,
)
```

(Runs daily at 00:05; `run_reset_if_due` internally no-ops unless today matches the configured reset day.)

- [ ] **Step 6: Run scheduler test to confirm nothing broke**

Run: `cd backend && pytest tests/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/reset.py backend/app/services/scheduler.py backend/tests/test_reset.py
git commit -m "feat(backend): add periodic accumulation reset job"
```

---

### Task 14: Purge job (retention)

**Files:**
- Create: `backend/app/services/purge.py`
- Modify: `backend/app/services/scheduler.py`
- Test: `backend/tests/test_purge.py`

**Interfaces:**
- Consumes: `TrafficSample` (Task 7), `AppSetting` (Task 8)
- Produces: `app.services.purge.purge_old_samples(db: Session, today: datetime | None = None) -> int` (returns count deleted)

- [ ] **Step 1: Write the failing test `backend/tests/test_purge.py`**

```python
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.settings import AppSetting
from app.models.traffic import TrafficSample
from app.services.purge import purge_old_samples


def test_purge_deletes_samples_older_than_retention():
    db: Session = SessionLocal()
    try:
        setting = db.get(AppSetting, "retention_days")
        original = setting.value
        setting.value = "90"
        db.commit()

        router = Router(name="Purge Test", host="10.0.0.6", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
        client = PPPoEClient(router_id=router.id, username="purge_user")
        db.add(client)
        db.flush()

        now = datetime.now(timezone.utc)
        old_sample = TrafficSample(client_id=client.id, sampled_at=now - timedelta(days=100))
        recent_sample = TrafficSample(client_id=client.id, sampled_at=now - timedelta(days=10))
        db.add_all([old_sample, recent_sample])
        db.commit()

        deleted = purge_old_samples(db, today=now)

        assert deleted == 1
        remaining = db.query(TrafficSample).filter_by(client_id=client.id).all()
        assert len(remaining) == 1
        assert remaining[0].id == recent_sample.id
    finally:
        setting.value = original
        db.commit()
        db.rollback()
        db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_purge.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `backend/app/services/purge.py`**

```python
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.models.traffic import TrafficSample


def purge_old_samples(db: Session, today: datetime | None = None) -> int:
    today = today or datetime.now(timezone.utc)
    setting = db.get(AppSetting, "retention_days")
    retention_days = int(setting.value) if setting else 90
    cutoff = today - timedelta(days=retention_days)

    deleted = (
        db.query(TrafficSample)
        .filter(TrafficSample.sampled_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def run_purge_job() -> None:
    db = SessionLocal()
    try:
        purge_old_samples(db)
    finally:
        db.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_purge.py -v`
Expected: PASS

- [ ] **Step 5: Register the daily purge job in `backend/app/services/scheduler.py`**

```python
from app.services.purge import run_purge_job

# inside start_scheduler(), after the reset job's add_job call:
scheduler.add_job(
    run_purge_job,
    trigger=CronTrigger(hour=0, minute=15),
    id="run_purge_job",
    replace_existing=True,
    max_instances=1,
)
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/purge.py backend/app/services/scheduler.py backend/tests/test_purge.py
git commit -m "feat(backend): add retention purge job for old traffic samples"
```

---

### Task 15: Notification senders (email + Telegram)

**Files:**
- Create: `backend/app/services/notifications.py`
- Test: `backend/tests/test_notifications.py`

**Interfaces:**
- Consumes: `AppSetting` (Task 8)
- Produces: `app.services.notifications.send_email(db: Session, subject: str, body: str) -> None`, `app.services.notifications.send_telegram(db: Session, message: str) -> None`, `app.services.notifications.notify(db: Session, subject: str, message: str) -> None` (calls whichever channels have complete settings configured; silently skips incomplete ones)

- [ ] **Step 1: Write the failing tests `backend/tests/test_notifications.py`**

```python
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import AppSetting
from app.services.notifications import notify


def _set(db: Session, key: str, value: str):
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value


def test_notify_calls_email_when_smtp_configured(monkeypatch):
    db = SessionLocal()
    try:
        for key, value in [
            ("smtp_host", "smtp.example.com"),
            ("smtp_port", "587"),
            ("smtp_username", "user"),
            ("smtp_password", "pass"),
            ("smtp_from", "alerts@example.com"),
            ("smtp_to", "admin@example.com"),
            ("telegram_bot_token", ""),
            ("telegram_chat_id", ""),
        ]:
            _set(db, key, value)
        db.commit()

        mock_send_email = MagicMock()
        monkeypatch.setattr("app.services.notifications._smtp_send", mock_send_email)

        notify(db, "Heavy user detected", "cliente1 superó el umbral")

        mock_send_email.assert_called_once()
    finally:
        db.rollback()
        db.close()


def test_notify_skips_channels_with_incomplete_settings(monkeypatch):
    db = SessionLocal()
    try:
        for key in ["smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from", "smtp_to",
                    "telegram_bot_token", "telegram_chat_id"]:
            _set(db, key, "")
        db.commit()

        mock_send_email = MagicMock()
        mock_send_telegram = MagicMock()
        monkeypatch.setattr("app.services.notifications._smtp_send", mock_send_email)
        monkeypatch.setattr("app.services.notifications._telegram_send", mock_send_telegram)

        notify(db, "subject", "message")

        mock_send_email.assert_not_called()
        mock_send_telegram.assert_not_called()
    finally:
        db.rollback()
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_notifications.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `backend/app/services/notifications.py`**

```python
import logging
import smtplib
from email.message import EmailMessage

import httpx
from sqlalchemy.orm import Session

from app.models.settings import AppSetting

logger = logging.getLogger(__name__)


def _get_settings(db: Session, keys: list[str]) -> dict[str, str]:
    rows = db.query(AppSetting).filter(AppSetting.key.in_(keys)).all()
    return {row.key: row.value for row in rows}


def _smtp_send(host: str, port: int, username: str, password: str, from_addr: str, to_addr: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = to_addr
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)


def _telegram_send(bot_token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    response = httpx.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
    response.raise_for_status()


def send_email(db: Session, subject: str, body: str) -> None:
    keys = ["smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from", "smtp_to"]
    values = _get_settings(db, keys)
    if not all(values.get(k) for k in keys):
        return
    try:
        _smtp_send(
            values["smtp_host"],
            int(values["smtp_port"]),
            values["smtp_username"],
            values["smtp_password"],
            values["smtp_from"],
            values["smtp_to"],
            subject,
            body,
        )
    except Exception:
        logger.exception("Failed to send email notification")


def send_telegram(db: Session, message: str) -> None:
    keys = ["telegram_bot_token", "telegram_chat_id"]
    values = _get_settings(db, keys)
    if not all(values.get(k) for k in keys):
        return
    try:
        _telegram_send(values["telegram_bot_token"], values["telegram_chat_id"], message)
    except Exception:
        logger.exception("Failed to send Telegram notification")


def notify(db: Session, subject: str, message: str) -> None:
    send_email(db, subject, message)
    send_telegram(db, message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_notifications.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/notifications.py backend/tests/test_notifications.py
git commit -m "feat(backend): add email and Telegram notification senders"
```

---

### Task 16: Alert evaluation after each poll

**Files:**
- Create: `backend/app/services/alerts.py`
- Modify: `backend/app/services/polling.py`
- Test: `backend/tests/test_alerts.py`

**Interfaces:**
- Consumes: `AlertThreshold`, `AlertEvent` (Task 8); `AccumulationPeriod` (Task 7); `notify` (Task 15)
- Produces: `app.services.alerts.evaluate_alerts(db: Session, client_id: int) -> None` (checks the client's applicable threshold — per-client override if present, else global `client_id=None` row — against its current open `AccumulationPeriod` total; if exceeded and no `AlertEvent` exists yet for this threshold within this period, creates one and calls `notify`)

- [ ] **Step 1: Write the failing tests `backend/tests/test_alerts.py`**

```python
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.alert import AlertEvent, AlertThreshold
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.traffic import AccumulationPeriod
from app.services.alerts import evaluate_alerts


def _setup(db: Session, threshold_bytes: int, accumulated_bytes: int, client_specific: bool):
    router = Router(name="Alert Test", host="10.0.0.7", api_username="a", api_password_encrypted="")
    db.add(router)
    db.flush()
    client = PPPoEClient(router_id=router.id, username="heavy_user")
    db.add(client)
    db.flush()
    threshold = AlertThreshold(
        client_id=client.id if client_specific else None,
        bytes_threshold=threshold_bytes,
        notify_channel="email",
    )
    db.add(threshold)
    period = AccumulationPeriod(client_id=client.id, rx_bytes_total=accumulated_bytes, tx_bytes_total=0)
    db.add(period)
    db.commit()
    return client, threshold


def test_evaluate_alerts_creates_event_and_notifies_when_over_threshold(monkeypatch):
    db = SessionLocal()
    try:
        client, threshold = _setup(db, threshold_bytes=1000, accumulated_bytes=2000, client_specific=True)
        mock_notify = MagicMock()
        monkeypatch.setattr("app.services.alerts.notify", mock_notify)

        evaluate_alerts(db, client.id)

        events = db.query(AlertEvent).filter_by(client_id=client.id).all()
        assert len(events) == 1
        mock_notify.assert_called_once()
    finally:
        db.rollback()
        db.close()


def test_evaluate_alerts_does_not_duplicate_event_in_same_period(monkeypatch):
    db = SessionLocal()
    try:
        client, threshold = _setup(db, threshold_bytes=1000, accumulated_bytes=2000, client_specific=True)
        monkeypatch.setattr("app.services.alerts.notify", MagicMock())

        evaluate_alerts(db, client.id)
        evaluate_alerts(db, client.id)

        events = db.query(AlertEvent).filter_by(client_id=client.id).all()
        assert len(events) == 1
    finally:
        db.rollback()
        db.close()


def test_evaluate_alerts_does_nothing_when_under_threshold(monkeypatch):
    db = SessionLocal()
    try:
        client, threshold = _setup(db, threshold_bytes=5000, accumulated_bytes=1000, client_specific=True)
        mock_notify = MagicMock()
        monkeypatch.setattr("app.services.alerts.notify", mock_notify)

        evaluate_alerts(db, client.id)

        assert db.query(AlertEvent).filter_by(client_id=client.id).count() == 0
        mock_notify.assert_not_called()
    finally:
        db.rollback()
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_alerts.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `backend/app/services/alerts.py`**

```python
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.alert import AlertEvent, AlertThreshold
from app.models.traffic import AccumulationPeriod
from app.services.notifications import notify


def evaluate_alerts(db: Session, client_id: int) -> None:
    period = (
        db.query(AccumulationPeriod)
        .filter_by(client_id=client_id, period_end=None)
        .first()
    )
    if period is None:
        return

    total_bytes = period.rx_bytes_total + period.tx_bytes_total

    threshold = (
        db.query(AlertThreshold).filter_by(client_id=client_id).first()
        or db.query(AlertThreshold).filter_by(client_id=None).first()
    )
    if threshold is None or total_bytes < threshold.bytes_threshold:
        return

    already_notified = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.client_id == client_id,
            AlertEvent.threshold_id == threshold.id,
            AlertEvent.triggered_at >= period.period_start,
        )
        .first()
    )
    if already_notified is not None:
        return

    db.add(
        AlertEvent(
            client_id=client_id,
            threshold_id=threshold.id,
            triggered_at=datetime.now(timezone.utc),
            accumulated_bytes_at_trigger=total_bytes,
        )
    )
    db.commit()

    notify(db, "Heavy user detectado", f"El cliente {client_id} superó el umbral configurado de {threshold.bytes_threshold} bytes.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_alerts.py -v`
Expected: PASS

- [ ] **Step 5: Call `evaluate_alerts` from `poll_router` in `backend/app/services/polling.py`**

Add the import and, inside the `for session in sessions:` loop right after the period totals are updated (after `period.tx_bytes_total += tx_delta`), add:

```python
from app.services.alerts import evaluate_alerts

# ... inside the loop, after period.tx_bytes_total += tx_delta:
evaluate_alerts(db, client.id)
```

- [ ] **Step 6: Run full polling test suite to confirm nothing broke**

Run: `cd backend && pytest tests/test_polling.py tests/test_alerts.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/alerts.py backend/app/services/polling.py backend/tests/test_alerts.py
git commit -m "feat(backend): evaluate and fire alert thresholds after each poll"
```

---

### Task 17: Dashboard summary endpoint

**Files:**
- Create: `backend/app/schemas/client.py` (partial — dashboard schema; extended in Task 18)
- Create: `backend/app/api/dashboard.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_dashboard.py`

**Interfaces:**
- Consumes: `PPPoEClient`, `Router` (Tasks 5-6), `get_current_user` (Task 3)
- Produces: `app.schemas.client.DashboardSummary` (`total_clients_connected: int, by_router: list[RouterSummary]`), `app.schemas.client.RouterSummary` (`router_id: int, router_name: str, clients_connected: int`), `GET /dashboard/summary`

- [ ] **Step 1: Write `backend/app/schemas/client.py`**

```python
from pydantic import BaseModel


class RouterSummary(BaseModel):
    router_id: int
    router_name: str
    clients_connected: int


class DashboardSummary(BaseModel):
    total_clients_connected: int
    by_router: list[RouterSummary]
```

- [ ] **Step 2: Write the failing test `backend/tests/test_api_dashboard.py`**

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.user import User

client = TestClient(app)


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "dash-tester").delete()
        db.commit()
        db.add(User(username="dash-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('dash-tester')}"}


def test_dashboard_summary_counts_active_clients_per_router():
    db: Session = SessionLocal()
    try:
        router = Router(name="Dash Router", host="10.0.0.8", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
        db.add(PPPoEClient(router_id=router.id, username="u1", is_active=True))
        db.add(PPPoEClient(router_id=router.id, username="u2", is_active=True))
        db.add(PPPoEClient(router_id=router.id, username="u3", is_active=False))
        db.commit()
        router_id = router.id
    finally:
        db.close()

    response = client.get("/dashboard/summary", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["total_clients_connected"] >= 2
    router_entry = next(r for r in body["by_router"] if r["router_id"] == router_id)
    assert router_entry["clients_connected"] == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_api_dashboard.py -v`
Expected: FAIL (404)

- [ ] **Step 4: Write `backend/app/api/dashboard.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.client import PPPoEClient
from app.models.router import Router
from app.schemas.client import DashboardSummary, RouterSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_user)])


@router.get("/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db)):
    rows = (
        db.query(Router.id, Router.name, func.count(PPPoEClient.id))
        .outerjoin(PPPoEClient, (PPPoEClient.router_id == Router.id) & (PPPoEClient.is_active.is_(True)))
        .group_by(Router.id, Router.name)
        .all()
    )
    by_router = [RouterSummary(router_id=r[0], router_name=r[1], clients_connected=r[2]) for r in rows]
    total = sum(r.clients_connected for r in by_router)
    return DashboardSummary(total_clients_connected=total, by_router=by_router)
```

- [ ] **Step 5: Wire into `backend/app/main.py`**

```python
from app.api.dashboard import router as dashboard_router

# after app.include_router(routers_router):
app.include_router(dashboard_router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest tests/test_api_dashboard.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/client.py backend/app/api/dashboard.py backend/app/main.py backend/tests/test_api_dashboard.py
git commit -m "feat(backend): add dashboard summary endpoint"
```

---

### Task 18: Clients list, detail, and history endpoints

**Files:**
- Modify: `backend/app/schemas/client.py`
- Create: `backend/app/api/clients.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_clients.py`

**Interfaces:**
- Consumes: `PPPoEClient`, `Router` (Tasks 5-6), `AccumulationPeriod`, `TrafficSample` (Task 7)
- Produces: `app.schemas.client.ClientOut` (`id, router_id, router_name, username, is_active, last_seen, accumulated_rx_bytes, accumulated_tx_bytes`), `app.schemas.client.ClientHistoryPoint` (`sampled_at, rx_bytes_delta, tx_bytes_delta`), `GET /clients?router_id=&active_only=&sort_by=accumulated` (sort_by one of `accumulated`, `username`), `GET /clients/{id}/history?hours=24`

- [ ] **Step 1: Extend `backend/app/schemas/client.py`**

Add to the existing file:

```python
from datetime import datetime


class ClientOut(BaseModel):
    id: int
    router_id: int
    router_name: str
    username: str
    is_active: bool
    last_seen: datetime
    accumulated_rx_bytes: int
    accumulated_tx_bytes: int

    model_config = {"from_attributes": True}


class ClientHistoryPoint(BaseModel):
    sampled_at: datetime
    rx_bytes_delta: int
    tx_bytes_delta: int
```

- [ ] **Step 2: Write the failing tests `backend/tests/test_api_clients.py`**

```python
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.traffic import AccumulationPeriod, TrafficSample
from app.models.user import User

client = TestClient(app)


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "clients-tester").delete()
        db.commit()
        db.add(User(username="clients-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('clients-tester')}"}


def _seed_client(db: Session, username: str, rx_total: int) -> int:
    router = db.query(Router).filter_by(name="Clients Router").first()
    if router is None:
        router = Router(name="Clients Router", host="10.0.0.10", api_username="a", api_password_encrypted="")
        db.add(router)
        db.flush()
    c = PPPoEClient(router_id=router.id, username=username, is_active=True)
    db.add(c)
    db.flush()
    db.add(AccumulationPeriod(client_id=c.id, rx_bytes_total=rx_total, tx_bytes_total=0))
    db.add(TrafficSample(client_id=c.id, sampled_at=datetime.now(timezone.utc), rx_bytes_delta=rx_total, tx_bytes_delta=0))
    db.commit()
    return c.id


def test_list_clients_sorted_by_accumulated_desc():
    db = SessionLocal()
    try:
        _seed_client(db, "low_user", 100)
        _seed_client(db, "heavy_user", 999999)
    finally:
        db.close()

    response = client.get("/clients?sort_by=accumulated", headers=_auth_headers())
    assert response.status_code == 200
    usernames = [c["username"] for c in response.json()]
    assert usernames.index("heavy_user") < usernames.index("low_user")


def test_client_history_returns_samples():
    db = SessionLocal()
    try:
        client_id = _seed_client(db, "history_user", 500)
    finally:
        db.close()

    response = client.get(f"/clients/{client_id}/history?hours=24", headers=_auth_headers())
    assert response.status_code == 200
    points = response.json()
    assert len(points) >= 1
    assert points[0]["rx_bytes_delta"] == 500
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_api_clients.py -v`
Expected: FAIL (404)

- [ ] **Step 4: Write `backend/app/api/clients.py`**

```python
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.client import PPPoEClient
from app.models.router import Router
from app.models.traffic import AccumulationPeriod, TrafficSample
from app.schemas.client import ClientHistoryPoint, ClientOut

router = APIRouter(prefix="/clients", tags=["clients"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[ClientOut])
def list_clients(
    router_id: int | None = None,
    active_only: bool = False,
    sort_by: str = Query("accumulated", pattern="^(accumulated|username)$"),
    db: Session = Depends(get_db),
):
    query = (
        db.query(
            PPPoEClient,
            Router.name.label("router_name"),
            func.coalesce(AccumulationPeriod.rx_bytes_total, 0).label("rx_total"),
            func.coalesce(AccumulationPeriod.tx_bytes_total, 0).label("tx_total"),
        )
        .join(Router, Router.id == PPPoEClient.router_id)
        .outerjoin(
            AccumulationPeriod,
            (AccumulationPeriod.client_id == PPPoEClient.id) & (AccumulationPeriod.period_end.is_(None)),
        )
    )
    if router_id is not None:
        query = query.filter(PPPoEClient.router_id == router_id)
    if active_only:
        query = query.filter(PPPoEClient.is_active.is_(True))

    rows = query.all()

    results = [
        ClientOut(
            id=c.id,
            router_id=c.router_id,
            router_name=router_name,
            username=c.username,
            is_active=c.is_active,
            last_seen=c.last_seen,
            accumulated_rx_bytes=rx_total,
            accumulated_tx_bytes=tx_total,
        )
        for c, router_name, rx_total, tx_total in rows
    ]

    if sort_by == "accumulated":
        results.sort(key=lambda r: r.accumulated_rx_bytes + r.accumulated_tx_bytes, reverse=True)
    else:
        results.sort(key=lambda r: r.username)

    return results


@router.get("/{client_id}/history", response_model=list[ClientHistoryPoint])
def client_history(client_id: int, hours: int = 24, db: Session = Depends(get_db)):
    client = db.get(PPPoEClient, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    samples = (
        db.query(TrafficSample)
        .filter(TrafficSample.client_id == client_id, TrafficSample.sampled_at >= since)
        .order_by(TrafficSample.sampled_at)
        .all()
    )
    return [
        ClientHistoryPoint(sampled_at=s.sampled_at, rx_bytes_delta=s.rx_bytes_delta, tx_bytes_delta=s.tx_bytes_delta)
        for s in samples
    ]
```

- [ ] **Step 5: Wire into `backend/app/main.py`**

```python
from app.api.clients import router as clients_router

# after app.include_router(dashboard_router):
app.include_router(clients_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_api_clients.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/client.py backend/app/api/clients.py backend/app/main.py backend/tests/test_api_clients.py
git commit -m "feat(backend): add clients list, sort, and history endpoints"
```

---

### Task 19: Settings endpoints

**Files:**
- Create: `backend/app/schemas/settings.py`
- Create: `backend/app/api/settings.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_settings.py`

**Interfaces:**
- Consumes: `AppSetting` (Task 8)
- Produces: `app.schemas.settings.SettingsOut` (dict of all known keys → values, SMTP/Telegram secrets masked), `app.schemas.settings.SettingsUpdate` (all fields optional), `GET /settings`, `PUT /settings`

- [ ] **Step 1: Write `backend/app/schemas/settings.py`**

```python
from pydantic import BaseModel


class SettingsOut(BaseModel):
    polling_interval_seconds: int
    reset_day_of_month: int
    retention_days: int
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    telegram_chat_id: str | None = None
    smtp_password_set: bool = False
    telegram_bot_token_set: bool = False


class SettingsUpdate(BaseModel):
    polling_interval_seconds: int | None = None
    reset_day_of_month: int | None = None
    retention_days: int | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_to: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
```

- [ ] **Step 2: Write the failing test `backend/tests/test_api_settings.py`**

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.user import User

client = TestClient(app)


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "settings-tester").delete()
        db.commit()
        db.add(User(username="settings-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('settings-tester')}"}


def test_get_settings_returns_defaults():
    response = client.get("/settings", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["polling_interval_seconds"] == 300
    assert body["reset_day_of_month"] == 1


def test_put_settings_updates_polling_interval():
    headers = _auth_headers()
    response = client.put("/settings", json={"polling_interval_seconds": 600}, headers=headers)
    assert response.status_code == 200
    assert response.json()["polling_interval_seconds"] == 600

    get_response = client.get("/settings", headers=headers)
    assert get_response.json()["polling_interval_seconds"] == 600
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_api_settings.py -v`
Expected: FAIL (404)

- [ ] **Step 4: Write `backend/app/api/settings.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.settings import AppSetting
from app.schemas.settings import SettingsOut, SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_user)])

_ALL_KEYS = [
    "polling_interval_seconds",
    "reset_day_of_month",
    "retention_days",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_from",
    "smtp_to",
    "telegram_bot_token",
    "telegram_chat_id",
]


def _read_all(db: Session) -> dict[str, str]:
    rows = db.query(AppSetting).filter(AppSetting.key.in_(_ALL_KEYS)).all()
    return {row.key: row.value for row in rows}


@router.get("", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)):
    values = _read_all(db)
    return SettingsOut(
        polling_interval_seconds=int(values.get("polling_interval_seconds", 300)),
        reset_day_of_month=int(values.get("reset_day_of_month", 1)),
        retention_days=int(values.get("retention_days", 90)),
        smtp_host=values.get("smtp_host") or None,
        smtp_port=int(values["smtp_port"]) if values.get("smtp_port") else None,
        smtp_username=values.get("smtp_username") or None,
        smtp_from=values.get("smtp_from") or None,
        smtp_to=values.get("smtp_to") or None,
        telegram_chat_id=values.get("telegram_chat_id") or None,
        smtp_password_set=bool(values.get("smtp_password")),
        telegram_bot_token_set=bool(values.get("telegram_bot_token")),
    )


@router.put("", response_model=SettingsOut)
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        row = db.get(AppSetting, key)
        str_value = str(value)
        if row is None:
            db.add(AppSetting(key=key, value=str_value))
        else:
            row.value = str_value
    db.commit()
    return get_settings(db)
```

- [ ] **Step 5: Wire into `backend/app/main.py`**

```python
from app.api.settings import router as settings_router

# after app.include_router(clients_router):
app.include_router(settings_router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && pytest tests/test_api_settings.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/settings.py backend/app/api/settings.py backend/app/main.py backend/tests/test_api_settings.py
git commit -m "feat(backend): add settings get/update endpoints"
```

---

### Task 20: Alert thresholds/events endpoints

**Files:**
- Create: `backend/app/schemas/alert.py`
- Create: `backend/app/api/alerts.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_alerts.py`

**Interfaces:**
- Consumes: `AlertThreshold`, `AlertEvent` (Task 8)
- Produces: `app.schemas.alert.ThresholdCreate/Out`, `app.schemas.alert.AlertEventOut`, `GET/POST/DELETE /alerts/thresholds`, `GET /alerts/events`

- [ ] **Step 1: Write `backend/app/schemas/alert.py`**

```python
from datetime import datetime

from pydantic import BaseModel


class ThresholdCreate(BaseModel):
    client_id: int | None = None
    bytes_threshold: int
    notify_channel: str = "email"


class ThresholdOut(BaseModel):
    id: int
    client_id: int | None
    bytes_threshold: int
    notify_channel: str

    model_config = {"from_attributes": True}


class AlertEventOut(BaseModel):
    id: int
    client_id: int
    threshold_id: int
    triggered_at: datetime
    accumulated_bytes_at_trigger: int

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Write the failing tests `backend/tests/test_api_alerts.py`**

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.database import SessionLocal
from app.main import app
from app.models.user import User

client = TestClient(app)


def _auth_headers() -> dict:
    db: Session = SessionLocal()
    try:
        db.query(User).filter(User.username == "alerts-tester").delete()
        db.commit()
        db.add(User(username="alerts-tester", password_hash=hash_password("x")))
        db.commit()
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token('alerts-tester')}"}


def test_create_list_delete_global_threshold():
    headers = _auth_headers()
    create_resp = client.post(
        "/alerts/thresholds",
        json={"bytes_threshold": 500_000_000_000, "notify_channel": "email"},
        headers=headers,
    )
    assert create_resp.status_code == 201
    threshold_id = create_resp.json()["id"]

    list_resp = client.get("/alerts/thresholds", headers=headers)
    assert any(t["id"] == threshold_id for t in list_resp.json())

    delete_resp = client.delete(f"/alerts/thresholds/{threshold_id}", headers=headers)
    assert delete_resp.status_code == 204


def test_list_events_returns_empty_list_when_none():
    response = client.get("/alerts/events", headers=_auth_headers())
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_api_alerts.py -v`
Expected: FAIL (404)

- [ ] **Step 4: Write `backend/app/api/alerts.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.alert import AlertEvent, AlertThreshold
from app.schemas.alert import AlertEventOut, ThresholdCreate, ThresholdOut

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(get_current_user)])


@router.get("/thresholds", response_model=list[ThresholdOut])
def list_thresholds(db: Session = Depends(get_db)):
    return db.query(AlertThreshold).all()


@router.post("/thresholds", response_model=ThresholdOut, status_code=status.HTTP_201_CREATED)
def create_threshold(payload: ThresholdCreate, db: Session = Depends(get_db)):
    obj = AlertThreshold(**payload.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/thresholds/{threshold_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_threshold(threshold_id: int, db: Session = Depends(get_db)):
    obj = db.get(AlertThreshold, threshold_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Threshold not found")
    db.delete(obj)
    db.commit()


@router.get("/events", response_model=list[AlertEventOut])
def list_events(db: Session = Depends(get_db)):
    return db.query(AlertEvent).order_by(AlertEvent.triggered_at.desc()).limit(200).all()
```

- [ ] **Step 5: Wire into `backend/app/main.py`**

```python
from app.api.alerts import router as alerts_router

# after app.include_router(settings_router):
app.include_router(alerts_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_api_alerts.py -v`
Expected: PASS

- [ ] **Step 7: Run the full backend test suite**

Run: `cd backend && pytest -v`
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/alert.py backend/app/api/alerts.py backend/app/main.py backend/tests/test_api_alerts.py
git commit -m "feat(backend): add alert thresholds and events endpoints"
```

---

### Task 21: Backend Dockerfile + docker-compose (db + backend)

**Files:**
- Create: `backend/Dockerfile`
- Create: `backend/entrypoint.sh`
- Create: `docker-compose.yml`
- Create: `.env.example`

**Interfaces:**
- Produces: a working `docker-compose up db backend` that serves the API on `localhost:8000`, running migrations automatically on container start.

- [ ] **Step 1: Write `backend/entrypoint.sh`**

```bash
#!/bin/sh
set -e

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: Write `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
```

- [ ] **Step 3: Write `.env.example` at the repo root**

```
POSTGRES_USER=pppoe
POSTGRES_PASSWORD=change-me
POSTGRES_DB=pppoe

DATABASE_URL=postgresql://pppoe:change-me@db:5432/pppoe
JWT_SECRET=change-me-to-a-long-random-string
MASTER_ENCRYPTION_KEY=generate-with-python-fernet
TOKEN_EXPIRE_MINUTES=480
```

- [ ] **Step 4: Write `docker-compose.yml` at the repo root**

```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - pppoe_db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 10

  backend:
    build: ./backend
    restart: unless-stopped
    environment:
      DATABASE_URL: ${DATABASE_URL}
      JWT_SECRET: ${JWT_SECRET}
      MASTER_ENCRYPTION_KEY: ${MASTER_ENCRYPTION_KEY}
      TOKEN_EXPIRE_MINUTES: ${TOKEN_EXPIRE_MINUTES}
    depends_on:
      db:
        condition: service_healthy
    expose:
      - "8000"

volumes:
  pppoe_db_data:
```

- [ ] **Step 5: Verify the stack builds and starts**

Run:
```bash
cp .env.example .env
# edit .env: set real POSTGRES_PASSWORD, JWT_SECRET, and a Fernet MASTER_ENCRYPTION_KEY
docker compose up -d db backend
sleep 5
curl localhost:8000/health
```
Expected: `{"status":"ok"}`, `docker compose ps` shows both containers healthy/running.

- [ ] **Step 6: Commit**

```bash
git add backend/Dockerfile backend/entrypoint.sh docker-compose.yml .env.example
git commit -m "feat(infra): add backend Dockerfile and docker-compose for db+backend"
```

---

### Task 22: Frontend scaffold + auth

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/context/AuthContext.tsx`
- Create: `frontend/src/components/ProtectedRoute.tsx`
- Create: `frontend/src/pages/Login.tsx`

**Interfaces:**
- Produces: `api.client.apiFetch(path, options)` (attaches JWT from `AuthContext`, throws on non-2xx), `AuthContext` (`token`, `login(username, password)`, `logout()`), `ProtectedRoute` component, `/login` route.

- [ ] **Step 1: Scaffold the Vite project**

Run: `cd <repo> && npm create vite@latest frontend -- --template react-ts`

- [ ] **Step 2: Install dependencies**

Run: `cd frontend && npm install && npm install react-router-dom recharts`

- [ ] **Step 3: Write `frontend/vite.config.ts`** (dev proxy to backend)

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
```

- [ ] **Step 4: Write `frontend/src/api/client.ts`**

```typescript
const TOKEN_STORAGE_KEY = 'pppoe_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token)
  else localStorage.removeItem(TOKEN_STORAGE_KEY)
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`/api${path}`, { ...options, headers })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`API error ${response.status}: ${detail}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
```

- [ ] **Step 5: Write `frontend/src/context/AuthContext.tsx`**

```typescript
import { createContext, useContext, useState, ReactNode } from 'react'
import { apiFetch, getToken, setToken } from '../api/client'

interface AuthContextValue {
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(!!getToken())

  async function login(username: string, password: string) {
    const result = await apiFetch<{ access_token: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setToken(result.access_token)
    setIsAuthenticated(true)
  }

  function logout() {
    setToken(null)
    setIsAuthenticated(false)
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
```

- [ ] **Step 6: Write `frontend/src/components/ProtectedRoute.tsx`**

```typescript
import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function ProtectedRoute() {
  const { isAuthenticated } = useAuth()
  return isAuthenticated ? <Outlet /> : <Navigate to="/login" replace />
}
```

- [ ] **Step 7: Write `frontend/src/pages/Login.tsx`**

```typescript
import { FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const { login } = useAuth()
  const navigate = useNavigate()

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await login(username, password)
      navigate('/')
    } catch {
      setError('Usuario o contraseña incorrectos')
    }
  }

  return (
    <div className="login-page">
      <form onSubmit={handleSubmit}>
        <h1>Monitoreo PPPoE</h1>
        <input placeholder="Usuario" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input
          type="password"
          placeholder="Contraseña"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="error">{error}</p>}
        <button type="submit">Ingresar</button>
      </form>
    </div>
  )
}
```

- [ ] **Step 8: Write `frontend/src/App.tsx`** (routes only wire Login for now; other pages added in Tasks 23-27)

```typescript
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Login } from './pages/Login'

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
```

- [ ] **Step 9: Write `frontend/src/main.tsx`**

```typescript
import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 10: Manually verify login works end-to-end**

Run:
```bash
cd backend && python3 -c "
from app.database import SessionLocal
from app.core.security import hash_password
from app.models.user import User
db = SessionLocal()
db.add(User(username='admin', password_hash=hash_password('changeme')))
db.commit()
"
cd ../frontend && npm run dev &
```
Open `http://localhost:5173/login` in a browser, log in with `admin`/`changeme`.
Expected: redirected to `/dashboard` (page not built yet — a blank/404-ish route is fine at this step, the key check is no auth error and the token is stored).

- [ ] **Step 11: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/tsconfig.json frontend/index.html frontend/src
git commit -m "feat(frontend): scaffold Vite app with JWT auth and login page"
```

---

### Task 23: Frontend Dashboard page

**Files:**
- Create: `frontend/src/pages/Dashboard.tsx`
- Create: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch` (Task 22), `GET /dashboard/summary` (Task 17)
- Produces: `Dashboard` page component, `Layout` component (nav bar shared by all authenticated pages)

- [ ] **Step 1: Write `frontend/src/components/Layout.tsx`**

```typescript
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function Layout() {
  const { logout } = useAuth()
  return (
    <div className="layout">
      <nav>
        <NavLink to="/dashboard">Dashboard</NavLink>
        <NavLink to="/clients">Clientes</NavLink>
        <NavLink to="/routers">Routers</NavLink>
        <NavLink to="/settings">Configuración</NavLink>
        <button onClick={logout}>Salir</button>
      </nav>
      <main>
        <Outlet />
      </main>
    </div>
  )
}
```

- [ ] **Step 2: Write `frontend/src/pages/Dashboard.tsx`**

```typescript
import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'

interface RouterSummary {
  router_id: number
  router_name: string
  clients_connected: number
}

interface DashboardSummary {
  total_clients_connected: number
  by_router: RouterSummary[]
}

export function Dashboard() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<DashboardSummary>('/dashboard/summary')
      .then(setSummary)
      .catch(() => setError('No se pudo cargar el resumen'))
  }, [])

  if (error) return <p className="error">{error}</p>
  if (!summary) return <p>Cargando...</p>

  return (
    <div className="dashboard">
      <div className="total-clients-card">
        <span className="label">Clientes conectados (total)</span>
        <span className="value">{summary.total_clients_connected}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Router</th>
            <th>Clientes conectados</th>
          </tr>
        </thead>
        <tbody>
          {summary.by_router.map((r) => (
            <tr key={r.router_id}>
              <td>{r.router_name}</td>
              <td>{r.clients_connected}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 3: Wire the route into `frontend/src/App.tsx`**

```typescript
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Layout } from './components/Layout'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<Layout />}>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
```

- [ ] **Step 4: Manually verify in browser**

Run: `cd frontend && npm run dev` (with backend running from Task 21)
Open `http://localhost:5173/login`, log in, confirm the dashboard shows the total and per-router breakdown (create a test router + poll data if the table is empty, or verify it renders "0" cleanly with no routers yet).
Expected: dashboard renders without console errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx frontend/src/components/Layout.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add dashboard page with total and per-router client counts"
```

---

### Task 24: Frontend Clients table (sortable, heavy-user ranking)

**Files:**
- Create: `frontend/src/pages/Clients.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch`, `GET /clients?sort_by=` (Task 18)
- Produces: `Clients` page with a table sortable by username/accumulated, linking each row to `/clients/:id`

- [ ] **Step 1: Write `frontend/src/pages/Clients.tsx`**

```typescript
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api/client'

interface ClientRow {
  id: number
  router_name: string
  username: string
  is_active: boolean
  accumulated_rx_bytes: number
  accumulated_tx_bytes: number
}

function formatBytes(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex++
  }
  return `${value.toFixed(2)} ${units[unitIndex]}`
}

export function Clients() {
  const [clients, setClients] = useState<ClientRow[]>([])
  const [sortBy, setSortBy] = useState<'accumulated' | 'username'>('accumulated')

  useEffect(() => {
    apiFetch<ClientRow[]>(`/clients?sort_by=${sortBy}`).then(setClients)
  }, [sortBy])

  return (
    <div className="clients-page">
      <div className="controls">
        <label>
          Ordenar por:
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value as 'accumulated' | 'username')}>
            <option value="accumulated">Consumo acumulado</option>
            <option value="username">Usuario</option>
          </select>
        </label>
      </div>
      <table>
        <thead>
          <tr>
            <th>Usuario</th>
            <th>Router</th>
            <th>Estado</th>
            <th>Acumulado (RX)</th>
            <th>Acumulado (TX)</th>
          </tr>
        </thead>
        <tbody>
          {clients.map((c) => (
            <tr key={c.id}>
              <td>
                <Link to={`/clients/${c.id}`}>{c.username}</Link>
              </td>
              <td>{c.router_name}</td>
              <td>{c.is_active ? 'Conectado' : 'Desconectado'}</td>
              <td>{formatBytes(c.accumulated_rx_bytes)}</td>
              <td>{formatBytes(c.accumulated_tx_bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Wire the route into `frontend/src/App.tsx`**

```typescript
import { Clients } from './pages/Clients'

// inside the <Route element={<Layout />}> block, after the dashboard route:
<Route path="/clients" element={<Clients />} />
```

- [ ] **Step 3: Manually verify in browser**

Open `http://localhost:5173/clients`, toggle the sort dropdown, confirm the order changes and heavy-users (highest accumulated) sort to the top.
Expected: table renders, sorting works, no console errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Clients.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add sortable clients table for heavy-user ranking"
```

---

### Task 25: Frontend Client detail page with chart

**Files:**
- Create: `frontend/src/pages/ClientDetail.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch`, `GET /clients/{id}/history?hours=` (Task 18), Recharts (`LineChart`, `Line`, `XAxis`, `YAxis`, `Tooltip`, `ResponsiveContainer`)
- Produces: `ClientDetail` page with a range selector (24h/7d) and a line chart of RX/TX over time

- [ ] **Step 1: Write `frontend/src/pages/ClientDetail.tsx`**

```typescript
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts'
import { apiFetch } from '../api/client'

interface HistoryPoint {
  sampled_at: string
  rx_bytes_delta: number
  tx_bytes_delta: number
}

const RANGES = [
  { label: 'Últimas 24h', hours: 24 },
  { label: 'Últimos 7 días', hours: 24 * 7 },
]

export function ClientDetail() {
  const { id } = useParams<{ id: string }>()
  const [hours, setHours] = useState(24)
  const [points, setPoints] = useState<HistoryPoint[]>([])

  useEffect(() => {
    if (!id) return
    apiFetch<HistoryPoint[]>(`/clients/${id}/history?hours=${hours}`).then(setPoints)
  }, [id, hours])

  const chartData = points.map((p) => ({
    time: new Date(p.sampled_at).toLocaleString(),
    RX: p.rx_bytes_delta,
    TX: p.tx_bytes_delta,
  }))

  return (
    <div className="client-detail-page">
      <div className="controls">
        {RANGES.map((r) => (
          <button key={r.hours} onClick={() => setHours(r.hours)} disabled={hours === r.hours}>
            {r.label}
          </button>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={400}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="time" />
          <YAxis />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="RX" stroke="#2563eb" dot={false} />
          <Line type="monotone" dataKey="TX" stroke="#dc2626" dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
```

- [ ] **Step 2: Wire the route into `frontend/src/App.tsx`**

```typescript
import { ClientDetail } from './pages/ClientDetail'

// inside the <Route element={<Layout />}> block, after the clients route:
<Route path="/clients/:id" element={<ClientDetail />} />
```

- [ ] **Step 3: Manually verify in browser**

Navigate from `/clients` to a client's detail page, toggle between 24h/7d ranges.
Expected: chart renders with RX/TX lines, range toggle refetches data, no console errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ClientDetail.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add client detail page with historical traffic chart"
```

---

### Task 26: Frontend Routers admin page

**Files:**
- Create: `frontend/src/pages/RoutersAdmin.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch`, `GET/POST/PUT/DELETE /routers` (Task 5)
- Produces: `RoutersAdmin` page with a form to add a router and a table listing/editing/deleting existing ones

- [ ] **Step 1: Write `frontend/src/pages/RoutersAdmin.tsx`**

```typescript
import { FormEvent, useEffect, useState } from 'react'
import { apiFetch } from '../api/client'

interface RouterRow {
  id: number
  name: string
  host: string
  port: number
  api_username: string
  use_tls: boolean
  verify_tls: boolean
  enabled: boolean
}

export function RoutersAdmin() {
  const [routers, setRouters] = useState<RouterRow[]>([])
  const [form, setForm] = useState({ name: '', host: '', port: 443, api_username: '', api_password: '' })
  const [error, setError] = useState<string | null>(null)

  function reload() {
    apiFetch<RouterRow[]>('/routers').then(setRouters)
  }

  useEffect(reload, [])

  async function handleCreate(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await apiFetch('/routers', { method: 'POST', body: JSON.stringify(form) })
      setForm({ name: '', host: '', port: 443, api_username: '', api_password: '' })
      reload()
    } catch {
      setError('No se pudo crear el router. Revisá los datos.')
    }
  }

  async function toggleEnabled(router: RouterRow) {
    await apiFetch(`/routers/${router.id}`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: !router.enabled }),
    })
    reload()
  }

  async function handleDelete(router: RouterRow) {
    if (!confirm(`¿Eliminar el router "${router.name}"?`)) return
    await apiFetch(`/routers/${router.id}`, { method: 'DELETE' })
    reload()
  }

  return (
    <div className="routers-admin-page">
      <form onSubmit={handleCreate}>
        <h2>Agregar router</h2>
        <input placeholder="Nombre" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input placeholder="Host/IP" value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} />
        <input
          type="number"
          placeholder="Puerto"
          value={form.port}
          onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
        />
        <input
          placeholder="Usuario API"
          value={form.api_username}
          onChange={(e) => setForm({ ...form, api_username: e.target.value })}
        />
        <input
          type="password"
          placeholder="Contraseña API"
          value={form.api_password}
          onChange={(e) => setForm({ ...form, api_password: e.target.value })}
        />
        {error && <p className="error">{error}</p>}
        <button type="submit">Agregar</button>
      </form>

      <table>
        <thead>
          <tr>
            <th>Nombre</th>
            <th>Host</th>
            <th>Habilitado</th>
            <th>Acciones</th>
          </tr>
        </thead>
        <tbody>
          {routers.map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td>{r.host}:{r.port}</td>
              <td>
                <input type="checkbox" checked={r.enabled} onChange={() => toggleEnabled(r)} />
              </td>
              <td>
                <button onClick={() => handleDelete(r)}>Eliminar</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Wire the route into `frontend/src/App.tsx`**

```typescript
import { RoutersAdmin } from './pages/RoutersAdmin'

// inside the <Route element={<Layout />}> block, after the client detail route:
<Route path="/routers" element={<RoutersAdmin />} />
```

- [ ] **Step 3: Manually verify in browser**

Add a router through the form, confirm it appears in the table, toggle its enabled checkbox, delete it.
Expected: all CRUD operations reflect immediately, no console errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/RoutersAdmin.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add routers admin page (CRUD)"
```

---

### Task 27: Frontend Settings & Alerts admin page

**Files:**
- Create: `frontend/src/pages/SettingsAdmin.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch`, `GET/PUT /settings` (Task 19), `GET/POST/DELETE /alerts/thresholds` (Task 20)
- Produces: `SettingsAdmin` page combining general settings (polling interval, reset day, retention, SMTP/Telegram config) and alert threshold management

- [ ] **Step 1: Write `frontend/src/pages/SettingsAdmin.tsx`**

```typescript
import { FormEvent, useEffect, useState } from 'react'
import { apiFetch } from '../api/client'

interface SettingsData {
  polling_interval_seconds: number
  reset_day_of_month: number
  retention_days: number
  smtp_host: string | null
  smtp_port: number | null
  smtp_username: string | null
  smtp_from: string | null
  smtp_to: string | null
  telegram_chat_id: string | null
}

interface Threshold {
  id: number
  client_id: number | null
  bytes_threshold: number
  notify_channel: string
}

export function SettingsAdmin() {
  const [settings, setSettings] = useState<SettingsData | null>(null)
  const [thresholds, setThresholds] = useState<Threshold[]>([])
  const [newThresholdGb, setNewThresholdGb] = useState(500)

  function reload() {
    apiFetch<SettingsData>('/settings').then(setSettings)
    apiFetch<Threshold[]>('/alerts/thresholds').then(setThresholds)
  }

  useEffect(reload, [])

  async function saveSettings(e: FormEvent) {
    e.preventDefault()
    if (!settings) return
    await apiFetch('/settings', { method: 'PUT', body: JSON.stringify(settings) })
    reload()
  }

  async function addGlobalThreshold(e: FormEvent) {
    e.preventDefault()
    await apiFetch('/alerts/thresholds', {
      method: 'POST',
      body: JSON.stringify({ client_id: null, bytes_threshold: newThresholdGb * 1024 ** 3, notify_channel: 'email' }),
    })
    reload()
  }

  async function deleteThreshold(id: number) {
    await apiFetch(`/alerts/thresholds/${id}`, { method: 'DELETE' })
    reload()
  }

  if (!settings) return <p>Cargando...</p>

  return (
    <div className="settings-admin-page">
      <form onSubmit={saveSettings}>
        <h2>Configuración general</h2>
        <label>
          Intervalo de polling (segundos)
          <input
            type="number"
            value={settings.polling_interval_seconds}
            onChange={(e) => setSettings({ ...settings, polling_interval_seconds: Number(e.target.value) })}
          />
        </label>
        <label>
          Día de reseteo mensual
          <input
            type="number"
            min={1}
            max={28}
            value={settings.reset_day_of_month}
            onChange={(e) => setSettings({ ...settings, reset_day_of_month: Number(e.target.value) })}
          />
        </label>
        <label>
          Retención de historial (días)
          <input
            type="number"
            value={settings.retention_days}
            onChange={(e) => setSettings({ ...settings, retention_days: Number(e.target.value) })}
          />
        </label>
        <button type="submit">Guardar</button>
      </form>

      <form onSubmit={addGlobalThreshold}>
        <h2>Umbral de alerta global (GB)</h2>
        <input type="number" value={newThresholdGb} onChange={(e) => setNewThresholdGb(Number(e.target.value))} />
        <button type="submit">Agregar umbral</button>
      </form>

      <table>
        <thead>
          <tr>
            <th>Alcance</th>
            <th>Umbral</th>
            <th>Canal</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {thresholds.map((t) => (
            <tr key={t.id}>
              <td>{t.client_id ? `Cliente #${t.client_id}` : 'Global'}</td>
              <td>{(t.bytes_threshold / 1024 ** 3).toFixed(1)} GB</td>
              <td>{t.notify_channel}</td>
              <td>
                <button onClick={() => deleteThreshold(t.id)}>Eliminar</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

- [ ] **Step 2: Wire the route into `frontend/src/App.tsx`**

```typescript
import { SettingsAdmin } from './pages/SettingsAdmin'

// inside the <Route element={<Layout />}> block, after the routers route:
<Route path="/settings" element={<SettingsAdmin />} />
```

- [ ] **Step 3: Manually verify in browser**

Update the polling interval and save, confirm it persists on reload. Add and delete a global threshold.
Expected: settings persist, threshold list updates, no console errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SettingsAdmin.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add settings and alert thresholds admin page"
```

---

### Task 28: Frontend Dockerfile + Nginx + docker-compose integration

**Files:**
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Modify: `docker-compose.yml`

**Interfaces:**
- Produces: a `frontend` container serving the built SPA on port 80, proxying `/api/*` to `backend:8000`

- [ ] **Step 1: Write `frontend/nginx.conf`**

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://backend:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 2: Write `frontend/Dockerfile`**

```dockerfile
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 3: Adjust `frontend/src/api/client.ts`** so production `fetch` calls hit `/api/...` (already does — the dev proxy in Task 22 already targets `/api`, and Nginx now also serves that same path in production, so no code change is needed; confirm by re-reading the file).

- [ ] **Step 4: Add the `frontend` service to `docker-compose.yml`**

```yaml
  frontend:
    build: ./frontend
    restart: unless-stopped
    depends_on:
      - backend
    ports:
      - "80:80"
```

- [ ] **Step 5: Verify the full stack builds and serves end-to-end**

Run:
```bash
docker compose up -d --build
sleep 10
curl -I localhost/
curl localhost/api/health
```
Expected: `curl -I localhost/` returns `200 OK` with HTML; `curl localhost/api/health` returns `{"status":"ok"}`.

- [ ] **Step 6: Commit**

```bash
git add frontend/Dockerfile frontend/nginx.conf docker-compose.yml
git commit -m "feat(infra): add frontend Dockerfile/Nginx and complete docker-compose stack"
```

---

### Task 29: README and deployment documentation

**Files:**
- Create: `README.md`
- Modify: `docs/superpowers/specs/2026-09-22-pppoe-monitor-design.md` (add a "Status" note, not content changes)

**Interfaces:**
- Produces: a README covering local dev setup, production deployment on the Ubuntu Server, environment variables, backup/restore, and creating the first admin user.

- [ ] **Step 1: Write `README.md`**

```markdown
# Sistema de Monitoreo PPPoE

Monitorea clientes PPPoE en múltiples routers Mikrotik: conectados, tráfico
actual y acumulado (con reseteo periódico), para detectar heavy-users.

Ver diseño completo en `docs/superpowers/specs/2026-09-22-pppoe-monitor-design.md`.

## Despliegue en producción (Ubuntu Server)

1. Instalar Docker y Docker Compose:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   ```
2. Clonar el repo y entrar al directorio.
3. Copiar `.env.example` a `.env` y completar:
   - `POSTGRES_PASSWORD`: contraseña fuerte para la base.
   - `JWT_SECRET`: string aleatorio largo (`openssl rand -hex 32`).
   - `MASTER_ENCRYPTION_KEY`: generar con
     `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
4. Levantar el stack:
   ```bash
   docker compose up -d --build
   ```
5. Crear el primer usuario admin:
   ```bash
   docker compose exec backend python3 -c "
   from app.database import SessionLocal
   from app.core.security import hash_password
   from app.models.user import User
   db = SessionLocal()
   db.add(User(username='admin', password_hash=hash_password('CAMBIAR-ESTA-PASSWORD')))
   db.commit()
   "
   ```
6. Acceder a `http://<ip-del-servidor>/` y cargar los routers desde
   "Routers" con sus credenciales de API REST (RouterOS 7.x).

## Desarrollo local

Backend:
```bash
docker run -d --name pppoe-dev-db -e POSTGRES_USER=pppoe -e POSTGRES_PASSWORD=pppoe -e POSTGRES_DB=pppoe -p 5432:5432 postgres:16
cd backend
pip install -r requirements.txt
cp .env.example ../.env  # o crear backend/.env con las mismas claves
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

## Tests

```bash
cd backend && pytest -v
```

## Backups

Backup manual de la base:
```bash
docker compose exec db pg_dump -U pppoe pppoe > backup-$(date +%Y%m%d).sql
```

Restaurar:
```bash
cat backup-YYYYMMDD.sql | docker compose exec -T db psql -U pppoe pppoe
```

Se recomienda programar el backup diario vía cron en el host.

## Configuración post-despliegue

Desde la pantalla "Configuración" en la web se puede ajustar:
- Intervalo de polling (default 5 min)
- Día de reseteo mensual del acumulado (default día 1)
- Retención del historial de gráficos (default 90 días)
- Credenciales SMTP y/o Telegram para alertas de heavy-users
- Umbrales de alerta (global o por cliente)
```

- [ ] **Step 2: Verify README instructions actually work by following them on a clean checkout (or mentally cross-check against Tasks 1-28)**

Cross-check each command against what earlier tasks actually created: `docker compose up -d --build` (Task 28), the admin-user creation snippet (matches `User`/`hash_password` from Task 3), `alembic upgrade head` (Task 2), `pytest -v` (all backend tasks). No mismatches.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add README with deployment and development instructions"
```

---

## Post-plan note: verification pending real router access

Task 10's `_match_interface_name` assumption (that RouterOS names each
PPPoE-server dynamic interface after the client's username) must be
verified against the user's actual Mikrotik routers once SSH/API access to
them exists. If their naming convention differs, only `mikrotik_client.py`
needs adjusting — no other task depends on the specific naming rule, only
on `fetch_active_sessions` returning correct `MikrotikSession` values.
