from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from ulis.api.security import extract_api_key, key_fingerprint, match_api_key
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
    # Constant-time comparison against every configured key (see ulis.api.security.match_api_key).
    key = match_api_key(extract_api_key(request.headers), settings.api_keys)
    if key is not None:
        # Identify the caller without ever logging the key itself.
        return Principal(key_fingerprint(key))
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED, "missing or invalid API key", headers={"WWW-Authenticate": "Bearer"}
    )
