import os
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="storix-test-"))
os.environ.update(
    {
        "STORIX_ENV": "development",
        "STORIX_SECRET_KEY": "test-secret-key-not-for-production-" + "x" * 32,
        "STORIX_SHARE_SECRET_KEY": "test-share-secret-key-not-for-production",
        "STORIX_DATABASE_URL": f"sqlite:///{(TEST_ROOT / 'test.db').as_posix()}",
        "STORIX_STORAGE_ROOT": str(TEST_ROOT / "storage"),
        "STORIX_BASE_URL": "http://testserver",
        "STORIX_ALLOW_SELF_PLAN_CHANGE": "true",
        "STORIX_LOGIN_RATE_LIMIT": "100000",
        "STORIX_REGISTER_RATE_LIMIT": "100000",
        "STORIX_UPLOAD_RATE_LIMIT": "100000",
        "STORIX_SHARE_RATE_LIMIT": "100000",
        "STORIX_ENABLE_DOCS": "false",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from database import SessionLocal, engine  # noqa: E402
from main import app  # noqa: E402
from models import Plan, User  # noqa: E402


def _reset_filesystem() -> None:
    engine.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = TEST_ROOT / f"test.db{suffix}"
        path.unlink(missing_ok=True)
    storage = TEST_ROOT / "storage"
    if storage.exists():
        shutil.rmtree(storage)
    (storage / ".tmp").mkdir(parents=True)


@pytest.fixture()
def client():
    _reset_filesystem()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def storage_root() -> Path:
    return TEST_ROOT / "storage"


def register(
    client: TestClient,
    username: str = "alice",
    password: str = "correct-horse-battery",
    email: str | None = None,
    full_name: str = "Alice Example",
):
    return client.post(
        "/auth/",
        json={
            "username": username,
            "email": email or f"{username}@example.com",
            "full_name": full_name,
            "password": password,
        },
    )


def login(
    client: TestClient,
    username: str = "alice",
    password: str = "correct-horse-battery",
):
    response = client.post(
        "/auth/token",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_user(
    client: TestClient,
    username: str = "alice",
    password: str = "correct-horse-battery",
    email: str | None = None,
) -> dict:
    response = register(client, username=username, password=password, email=email)
    assert response.status_code == 201, response.text
    return login(client, username=username, password=password)


def upload(
    client: TestClient,
    token: str,
    filename: str = "hello.txt",
    content: bytes = b"hello world",
    folder_id: int | None = None,
):
    params = {"folder_id": folder_id} if folder_id is not None else None
    return client.post(
        "/files/upload/",
        headers=auth(token),
        params=params,
        files=[("files", (filename, content, "application/octet-stream"))],
    )


def upload_one(
    client: TestClient,
    token: str,
    filename: str = "hello.txt",
    content: bytes = b"hello world",
    folder_id: int | None = None,
) -> dict:
    response = upload(client, token, filename, content, folder_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["uploaded"], body
    return body["uploaded"][0]


def set_plan(db, user_id: int, plan_name: str) -> None:
    plan = db.query(Plan).filter(Plan.name == plan_name).one()
    user = db.get(User, user_id)
    assert user is not None
    user.plan_id = plan.id
    db.commit()


def usage(client: TestClient, token: str) -> int:
    response = client.get("/auth/me", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()["used_storage"]
