from .conftest import auth, create_user, login, register


def test_register_login_and_me(client):
    register(client)
    duplicate = register(client)
    assert duplicate.status_code == 409

    tokens = login(client)
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"

    me = client.get("/auth/me", headers=auth(tokens["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == "alice"
    assert body["plan"]["name"] == "Free"
    assert body["used_storage"] == 0
    assert body["can_change_plan"] is True


def test_registration_validation_and_case_insensitive_duplicates(client):
    bad_username = client.post(
        "/auth/",
        json={
            "username": "../etc",
            "email": "x@example.com",
            "full_name": "X",
            "password": "correct-horse-battery",
        },
    )
    assert bad_username.status_code == 422

    short_password = client.post(
        "/auth/",
        json={
            "username": "shorty",
            "email": "shorty@example.com",
            "full_name": "Shorty",
            "password": "short",
        },
    )
    assert short_password.status_code == 422

    assert register(client, username="Alice", email="Case@Example.com").status_code == 201
    by_username = register(client, username="alice", email="other@example.com")
    assert by_username.status_code == 409
    by_email = register(client, username="alice2", email="case@example.com")
    assert by_email.status_code == 409


def test_login_failures_do_not_leak_account_existence(client):
    register(client)
    wrong = client.post(
        "/auth/token", data={"username": "alice", "password": "wrong-password"}
    )
    missing = client.post(
        "/auth/token", data={"username": "nobody", "password": "wrong-password"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"]
    assert client.get("/auth/me").status_code == 401


def test_refresh_rotation_and_logout_revocation(client):
    tokens = create_user(client)
    refreshed = client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    rotated = refreshed.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]

    # The newly issued refresh token remains usable.
    again = client.post(
        "/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    )
    assert again.status_code == 200

    # Logout deletes the persisted session immediately.
    logout = client.post(
        "/auth/logout", json={"refresh_token": again.json()["refresh_token"]}
    )
    assert logout.status_code == 204
    stale = client.post(
        "/auth/refresh", json={"refresh_token": again.json()["refresh_token"]}
    )
    assert stale.status_code == 401

    # Logout without a body is also accepted.
    assert client.post("/auth/logout").status_code == 204


def test_refresh_rejects_access_tokens_and_garbage(client):
    tokens = create_user(client)
    access_as_refresh = client.post(
        "/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert access_as_refresh.status_code == 401
    garbage = client.post("/auth/refresh", json={"refresh_token": "x" * 40})
    assert garbage.status_code == 401


def test_refresh_token_cannot_be_forged(client):
    from jose import jwt

    from config import ALGORITHM, SECRET_KEY

    tokens = create_user(client)
    payload = jwt.decode(
        tokens["refresh_token"], SECRET_KEY, algorithms=[ALGORITHM],
        audience="storix-api", issuer="storix",
    )
    payload["jti"] = "forged-jti"
    forged = jwt.encode(payload, "not-the-secret", algorithm=ALGORITHM)
    response = client.post("/auth/refresh", json={"refresh_token": forged})
    assert response.status_code == 401


def test_security_headers_and_health(client):
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["referrer-policy"] == "no-referrer"

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
