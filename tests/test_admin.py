from models import Plan, User

from .conftest import auth, create_user, login, register, upload_one, usage


def promote(db, username: str) -> None:
    user = db.query(User).filter(User.username == username).one()
    user.role = "admin"
    db.commit()


def test_admin_endpoints_require_admin(client):
    token = create_user(client)["access_token"]
    response = client.get("/admin/users", headers=auth(token))
    assert response.status_code == 403
    assert "Admin" in response.json()["detail"]


def test_admin_can_list_and_change_plans(client, db):
    admin = create_user(client, username="root")
    promote(db, "root")
    member = create_user(client, username="member")

    users = client.get("/admin/users", headers=auth(admin["access_token"]))
    assert users.status_code == 200, users.text
    assert {user["username"] for user in users.json()} == {"root", "member"}

    member_id = client.get(
        "/auth/me", headers=auth(member["access_token"])
    ).json()["id"]
    changed = client.put(
        f"/admin/users/{member_id}/plan",
        headers=auth(admin["access_token"]),
        json={"plan": "Premium"},
    )
    assert changed.status_code == 200, changed.text
    assert "Premium" in changed.json()["message"]

    me = client.get("/auth/me", headers=auth(member["access_token"])).json()
    assert me["plan"]["name"] == "Premium"

    # Unknown plans and users are rejected cleanly.
    assert client.put(
        f"/admin/users/{member_id}/plan",
        headers=auth(admin["access_token"]),
        json={"plan": "Platinum"},
    ).status_code == 404
    assert client.put(
        "/admin/users/999999/plan",
        headers=auth(admin["access_token"]),
        json={"plan": "Pro"},
    ).status_code == 404


def test_admin_cannot_delete_self_and_deletes_other_users(client, db, storage_root):
    admin = create_user(client, username="root")
    promote(db, "root")
    member = create_user(client, username="member")
    file = upload_one(client, member["access_token"], "member.txt", b"member data")

    me = client.get("/auth/me", headers=auth(admin["access_token"])).json()
    member_id = client.get(
        "/auth/me", headers=auth(member["access_token"])
    ).json()["id"]

    assert client.delete(
        f"/admin/users/{me['id']}", headers=auth(admin["access_token"])
    ).status_code == 400

    deleted = client.delete(
        f"/admin/users/{member_id}", headers=auth(admin["access_token"])
    )
    assert deleted.status_code == 200, deleted.text
    assert client.post(
        "/auth/token",
        data={"username": "member", "password": "correct-horse-battery"},
    ).status_code == 401

    # All blobs for the deleted account are gone.
    member_dir = storage_root / "users" / str(member_id)
    assert not member_dir.exists() or not any(member_dir.rglob("*.*"))

    assert file["id"]  # uploaded before deletion


def test_admin_reconcile_storage(client, db):
    admin = create_user(client, username="root")
    promote(db, "root")
    member = create_user(client, username="member")
    upload_one(client, member["access_token"], "a.txt", b"123456")

    member_id = client.get(
        "/auth/me", headers=auth(member["access_token"])
    ).json()["id"]
    user = db.get(User, member_id)
    user.used_storage = 0
    db.commit()

    response = client.post(
        "/admin/reconcile-storage", headers=auth(admin["access_token"])
    )
    assert response.status_code == 200, response.text
    assert response.json()["changed"] >= 1

    db.expire_all()
    assert db.get(User, member_id).used_storage == 6


def test_self_service_plan_change_can_be_disabled(client, db, monkeypatch):
    import routes.plans as plans_module

    token = create_user(client)["access_token"]
    monkeypatch.setattr(plans_module, "ALLOW_SELF_PLAN_CHANGE", False)
    response = client.put(
        "/plans/change-plan", headers=auth(token), json={"plan": "Pro"}
    )
    assert response.status_code == 403

    promote(db, "alice")
    allowed = client.put(
        "/plans/change-plan", headers=auth(token), json={"plan": "Pro"}
    )
    assert allowed.status_code == 200, allowed.text


def test_plan_listing_comes_from_database(client):
    plans = client.get("/plans/")
    assert plans.status_code == 200
    names = [plan["name"] for plan in plans.json()]
    assert names == ["Free", "Pro", "Premium"]
    for plan in plans.json():
        assert {"storage_limit", "max_file_size", "can_share", "price"} <= set(plan)
