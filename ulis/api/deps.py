from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.settings import Settings
from ulis.storage.object_store import ObjectStore


@dataclass
class AppState:
    settings: Settings
    catalog: Catalog
    session_factory: sessionmaker[Session]
    store: ObjectStore


def state(request: Request) -> AppState:
    return request.app.state.ulis  # type: ignore[no-any-return]


def get_session(app_state: AppState = Depends(state)) -> Iterator[Session]:  # noqa: B008
    session = app_state.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass(frozen=True)
class Principal:
    id: str


def require_api_key(request: Request, app_state: AppState = Depends(state)) -> Principal:  # noqa: B008
    settings = app_state.settings
    if not settings.requires_api_key:
        return Principal("anonymous-dev")
    supplied = request.headers.get("x-api-key")
    auth = request.headers.get("authorization", "")
    if not supplied and auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if supplied:
        for key in settings.api_keys:
            if hmac.compare_digest(supplied.encode(), key.encode()):
                # Identify the caller without ever logging the key itself.
                return Principal("key:" + hashlib.sha256(key.encode()).hexdigest()[:12])
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED, "missing or invalid API key", headers={"WWW-Authenticate": "Bearer"}
    )
