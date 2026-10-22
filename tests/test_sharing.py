from datetime import timedelta

from routes.auth import create_share_token

from .conftest import auth, create_user, set_plan, upload_one


def make_pro(client, db):
    tokens = create_user(client)
    set_plan(db, client.get("/auth/me", headers=auth(tokens["access_token"])).json()["id"], "Pro")
    return tokens


def test_free_plan_cannot_share_or_publish(client):
    token = create_user(client)["access_token"]
    file_id = upload_one(client, token)["id"]

    share = client.post(f"/files/share/{file_id}", headers=auth(token))
    assert share.status_code == 403

    public = client.put(
        f"/files/{file_id}/visibility",
        headers=auth(token),
        json={"choice": True},
    )
    assert public.status_code == 403


def test_share_link_lifecycle(client, db):
    token = make_pro(client, db)["access_token"]
    file = upload_one(client, token, "shared.txt", b"shared data")
    file_id = file["id"]

    created = client.post(
        f"/files/share/{file_id}?expires_minutes=1440", headers=auth(token)
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["share_url"].startswith("http://testserver/shared.html?token=")
    assert body["expires_minutes"] == 1440
    token_value = body["share_url"].split("token=", 1)[1]

    anonymous = client.get(f"/files/shared/{token_value}")
    assert anonymous.status_code == 200, anonymous.text
    assert anonymous.content == b"shared data"
    assert anonymous.headers["cache-control"] == "private, no-store"

    # The link is tied to the version group, not a mutable name.
    assert client.put(
        f"/files/{file_id}/rename",
        headers=auth(token),
        json={"filename": "renamed.txt"},
    ).status_code == 200
    assert client.get(f"/files/shared/{token_value}").content == b"shared data"

    # Trashing the file makes the link inert; restoring revives it.
    assert client.delete(
        f"/files/file-delete/{file_id}", headers=auth(token)
    ).status_code == 200
    assert client.get(f"/files/shared/{token_value}").status_code == 404
    assert client.post(
        f"/files/restore/{file_id}", headers=auth(token)
    ).status_code == 200
    assert client.get(f"/files/shared/{token_value}").status_code == 200

    # Downgrading the owner disables existing links.
    set_plan(db, client.get("/auth/me", headers=auth(token)).json()["id"], "Free")
    assert client.get(f"/files/shared/{token_value}").status_code == 403


def test_share_links_reject_expired_and_forged_tokens(client, db):
    token = make_pro(client, db)["access_token"]
    file = upload_one(client, token)
    version_group = client.get(
        "/files/get-files", headers=auth(token)
    ).json()[0]["version_group"]

    expired, _ = create_share_token(version_group=version_group, expires_minutes=-1)
    assert client.get(f"/files/shared/{expired}").status_code == 401
    assert client.get("/files/shared/not-a-token").status_code == 401

    # An access token is not a share token (different audience/type).
    access = token
    assert client.get(f"/files/shared/{access}").status_code == 401


def test_public_files_follow_plan_and_visibility(client, db):
    owner = make_pro(client, db)
    other = create_user(client, username="bob")
    file = upload_one(client, owner["access_token"], "public.txt", b"public data")

    assert client.put(
        f"/files/{file['id']}/visibility",
        headers=auth(owner["access_token"]),
        json={"choice": True},
    ).status_code == 200

    anonymous = client.get(f"/files/download/{file['id']}")
    assert anonymous.status_code == 200
    assert anonymous.content == b"public data"
    assert client.get(f"/files/preview/{file['id']}").status_code == 200

    # Public files are readable by any signed-in account too.
    assert client.get(
        f"/files/download/{file['id']}", headers=auth(other["access_token"])
    ).status_code == 200

    assert client.put(
        f"/files/{file['id']}/visibility",
        headers=auth(owner["access_token"]),
        json={"choice": False},
    ).status_code == 200
    assert client.get(f"/files/download/{file['id']}").status_code == 401
    assert client.get(
        f"/files/download/{file['id']}", headers=auth(other["access_token"])
    ).status_code == 404


def test_downgrade_revokes_public_files(client, db):
    owner = make_pro(client, db)
    file = upload_one(client, owner["access_token"], "public.txt", b"public data")
    assert client.put(
        f"/files/{file['id']}/visibility",
        headers=auth(owner["access_token"]),
        json={"choice": True},
    ).status_code == 200
    assert client.get(f"/files/download/{file['id']}").status_code == 200

    changed = client.put(
        "/plans/change-plan",
        headers=auth(owner["access_token"]),
        json={"plan": "Free"},
    )
    assert changed.status_code == 200, changed.text

    listed = client.get(
        "/files/get-files", headers=auth(owner["access_token"])
    ).json()
    assert listed[0]["is_public"] is False
    assert client.get(f"/files/download/{file['id']}").status_code == 401
    # The owner never loses access to their own file.
    assert client.get(
        f"/files/download/{file['id']}", headers=auth(owner["access_token"])
    ).status_code == 200


def test_private_file_requires_owner_even_with_id(client, db):
    owner = make_pro(client, db)
    other = create_user(client, username="bob")
    file = upload_one(client, owner["access_token"], "private.txt", b"private")

    assert client.get(f"/files/download/{file['id']}").status_code == 401
    assert client.get(
        f"/files/download/{file['id']}", headers=auth(other["access_token"])
    ).status_code == 404
    assert client.get(
        f"/files/preview/{file['id']}", headers=auth(other["access_token"])
    ).status_code == 404


def test_preview_content_type_safety(client):
    token = create_user(client)["access_token"]

    html = upload_one(client, token, "attack.html", b"<script>alert(1)</script>")
    svg = upload_one(
        client, token, "image.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'
    )
    text = upload_one(client, token, "notes.txt", b"plain text")

    html_preview = client.get(f"/files/preview/{html['id']}", headers=auth(token))
    assert html_preview.headers["content-type"] == "application/octet-stream"
    assert html_preview.headers["content-disposition"].startswith("attachment")
    assert html_preview.headers["x-content-type-options"] == "nosniff"

    svg_preview = client.get(f"/files/preview/{svg['id']}", headers=auth(token))
    assert svg_preview.headers["content-type"] == "application/octet-stream"
    assert svg_preview.headers["content-disposition"].startswith("attachment")

    text_preview = client.get(f"/files/preview/{text['id']}", headers=auth(token))
    assert text_preview.headers["content-type"].startswith("text/plain")
    assert text_preview.headers["content-disposition"].startswith("inline")
    assert "sandbox" in text_preview.headers["content-security-policy"]
    assert text_preview.headers["x-content-type-options"] == "nosniff"


def test_share_expiry_is_bounded(client, db):
    token = make_pro(client, db)["access_token"]
    file = upload_one(client, token)
    too_long = client.post(
        f"/files/share/{file['id']}?expires_minutes=999999", headers=auth(token)
    )
    assert too_long.status_code == 422
    zero = client.post(
        f"/files/share/{file['id']}?expires_minutes=0", headers=auth(token)
    )
    assert zero.status_code == 422
