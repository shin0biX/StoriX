import io
import zipfile
from pathlib import Path

from config import STORAGE_ROOT
from models import FileTable

from .conftest import (
    auth,
    create_user,
    set_plan,
    upload,
    upload_one,
    usage,
)


def test_upload_sanitizes_name_and_tracks_storage(client):
    token = create_user(client)["access_token"]
    file = upload_one(client, token, "../../evil name.txt", b"abc")
    assert file["filename"] == "evil name.txt"
    assert file["size"] == 3
    assert file["version"] == 1

    listed = client.get("/files/get-files", headers=auth(token))
    assert listed.status_code == 200
    assert [row["filename"] for row in listed.json()] == ["evil name.txt"]
    assert usage(client, token) == 3

    row = client.get("/files/versions/%s" % file["id"], headers=auth(token)).json()
    assert len(row) == 1


def test_quota_is_atomic_and_permanent_delete_frees_it(client, db):
    from models import Plan

    plan = db.query(Plan).filter(Plan.name == "Free").one()
    plan.storage_limit = 20
    db.commit()

    token = create_user(client)["access_token"]
    first = upload(client, token, "a.bin", b"a" * 16)
    assert first.status_code == 200, first.text
    assert len(first.json()["uploaded"]) == 1

    second = upload(client, token, "b.bin", b"b" * 16)
    assert second.status_code == 400
    assert "Storage limit exceeded" in second.json()["detail"]
    assert usage(client, token) == 16

    file_id = first.json()["uploaded"][0]["id"]
    assert client.delete(f"/files/file-delete/{file_id}", headers=auth(token)).status_code == 200
    assert usage(client, token) == 16
    assert client.delete(
        f"/files/permanent-delete/{file_id}", headers=auth(token)
    ).status_code == 200
    assert usage(client, token) == 0

    # The freed quota is usable again.
    third = upload(client, token, "c.bin", b"c" * 16)
    assert third.status_code == 200, third.text


def test_multi_file_upload_reports_partial_quota_failure(client, db):
    from models import Plan

    plan = db.query(Plan).filter(Plan.name == "Free").one()
    plan.storage_limit = 20
    db.commit()

    token = create_user(client)["access_token"]
    response = client.post(
        "/files/upload/",
        headers=auth(token),
        files=[
            ("files", ("one.bin", b"1" * 12, "application/octet-stream")),
            ("files", ("two.bin", b"2" * 12, "application/octet-stream")),
        ],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["uploaded"]) == 1
    assert len(body["errors"]) == 1
    assert "Storage limit exceeded" in body["errors"][0]["detail"]
    assert usage(client, token) == 12


def test_plan_max_file_size_is_enforced(client, db):
    from models import Plan

    plan = db.query(Plan).filter(Plan.name == "Free").one()
    plan.max_file_size = 10
    db.commit()

    token = create_user(client)["access_token"]
    response = upload(client, token, "big.bin", b"x" * 11)
    assert response.status_code == 400
    assert "max file size" in response.json()["detail"].lower()


def test_file_isolation_between_users(client):
    alice = create_user(client, username="alice")["access_token"]
    bob = create_user(client, username="bob")["access_token"]
    file_id = upload_one(client, alice)["id"]

    for method, path in [
        ("get", f"/files/download/{file_id}"),
        ("get", f"/files/preview/{file_id}"),
        ("get", f"/files/versions/{file_id}"),
        ("delete", f"/files/file-delete/{file_id}"),
        ("delete", f"/files/permanent-delete/{file_id}"),
    ]:
        response = getattr(client, method)(path, headers=auth(bob))
        assert response.status_code == 404, (method, path, response.text)

    star = client.put(
        f"/files/{file_id}/star", headers=auth(bob), json={"starred": True}
    )
    assert star.status_code == 404
    rename = client.put(
        f"/files/{file_id}/rename",
        headers=auth(bob),
        json={"filename": "stolen.txt"},
    )
    assert rename.status_code == 404

    zipped = client.get(
        f"/files/download-zip?ids={file_id}", headers=auth(bob)
    )
    assert zipped.status_code == 404


def test_versioning_restore_and_group_operations(client):
    token = create_user(client)["access_token"]

    v1 = upload_one(client, token, "report.txt", b"version one")
    v2 = upload_one(client, token, "report.txt", b"version two!")
    assert v1["id"] != v2["id"]
    assert v1["version"] == 1
    assert v2["version"] == 2

    listed = client.get("/files/get-files", headers=auth(token)).json()
    assert len(listed) == 1
    assert listed[0]["id"] == v2["id"]
    assert listed[0]["version"] == 2

    versions = client.get(
        f"/files/versions/{v2['id']}", headers=auth(token)
    ).json()
    assert [item["version"] for item in versions] == [2, 1]
    assert versions[0]["is_latest"] is True

    restore = client.post(
        f"/files/versions/{v2['id']}/restore/{v1['id']}", headers=auth(token)
    )
    assert restore.status_code == 200, restore.text
    listed = client.get("/files/get-files", headers=auth(token)).json()
    assert listed[0]["id"] == v1["id"]
    assert listed[0]["version"] == 1

    renamed = client.put(
        f"/files/{v1['id']}/rename",
        headers=auth(token),
        json={"filename": "renamed.txt"},
    )
    assert renamed.status_code == 200, renamed.text
    versions = client.get(
        f"/files/versions/{v1['id']}", headers=auth(token)
    ).json()
    assert {item["filename"] for item in versions} == {"renamed.txt"}

    starred = client.put(
        f"/files/{v1['id']}/star", headers=auth(token), json={"starred": True}
    )
    assert starred.status_code == 200
    listed = client.get("/files/get-files", headers=auth(token)).json()
    assert listed[0]["is_starred"] is True

    # Deleting the active row trashes the whole group and its quota remains.
    assert client.delete(
        f"/files/file-delete/{v1['id']}", headers=auth(token)
    ).status_code == 200
    assert client.get("/files/get-files", headers=auth(token)).json() == []
    trash = client.get("/files/trash", headers=auth(token)).json()
    assert len(trash) == 1
    assert trash[0]["id"] == v1["id"]
    assert usage(client, token) == len(b"version one") + len(b"version two!")

    # Restoring brings back only the selected latest row.
    assert client.post(
        f"/files/restore/{v1['id']}", headers=auth(token)
    ).status_code == 200
    assert len(client.get("/files/get-files", headers=auth(token)).json()) == 1

    # Uploading after a restore creates the next version number, not a duplicate.
    v3 = upload_one(client, token, "renamed.txt", b"version three")
    assert v3["version"] == 3


def test_trash_empty_frees_all_versions(client):
    token = create_user(client)["access_token"]
    upload_one(client, token, "a.txt", b"one")
    latest = upload_one(client, token, "a.txt", b"two")
    assert usage(client, token) == 6

    assert client.delete(
        f"/files/file-delete/{latest['id']}", headers=auth(token)
    ).status_code == 200
    response = client.post("/files/trash/empty", headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["files"] == 1
    assert response.json()["freed_bytes"] == 6
    assert usage(client, token) == 0
    assert client.get("/files/trash", headers=auth(token)).json() == []


def test_folders_nested_delete_restore_and_cycles(client):
    token = create_user(client)["access_token"]

    root = client.post(
        "/files/folders", headers=auth(token), json={"name": "Root"}
    )
    assert root.status_code == 201, root.text
    root_id = root.json()["id"]

    child = client.post(
        "/files/folders",
        headers=auth(token),
        json={"name": "Child", "parent_id": root_id},
    )
    assert child.status_code == 201
    child_id = child.json()["id"]

    duplicate = client.post(
        "/files/folders", headers=auth(token), json={"name": "root"}
    )
    assert duplicate.status_code == 409

    cycle = client.put(
        f"/files/folders/{root_id}/move",
        headers=auth(token),
        json={"parent_id": child_id},
    )
    assert cycle.status_code == 400

    file = upload_one(client, token, "nested.txt", b"nested", folder_id=child_id)
    assert file["folder_id"] == child_id

    deleted = client.delete(
        f"/files/folders/{root_id}", headers=auth(token)
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get("/files/folders", headers=auth(token)).json() == []

    trash = client.get("/files/trash", headers=auth(token)).json()
    assert len(trash) == 1
    assert trash[0]["folder_id"] is None

    restored = client.post(
        f"/files/restore/{trash[0]['id']}", headers=auth(token)
    )
    assert restored.status_code == 200, restored.text
    listed = client.get("/files/get-files", headers=auth(token)).json()
    assert listed[0]["folder_id"] is None


def test_move_file_and_name_conflicts(client):
    token = create_user(client)["access_token"]
    folder = client.post(
        "/files/folders", headers=auth(token), json={"name": "Docs"}
    ).json()

    first = upload_one(client, token, "same.txt", b"first")
    second = upload_one(client, token, "same.txt", b"second", folder_id=folder["id"])
    assert first["id"] != second["id"]

    # Moving the root file into the folder collides with the file already there.
    conflict = client.put(
        f"/files/{first['id']}/move",
        headers=auth(token),
        json={"folder_id": folder["id"]},
    )
    assert conflict.status_code == 409

    moved = client.put(
        f"/files/{first['id']}/move",
        headers=auth(token),
        json={"folder_id": None},
    )
    assert moved.status_code == 200


def test_zip_download_includes_files_and_sanitizes_legacy_names(client, db):
    token = create_user(client)["access_token"]
    one = upload_one(client, token, "one.txt", b"one")
    two = upload_one(client, token, "two.txt", b"two")

    # A legacy row may contain a path-like display name; the archive must not.
    db.query(FileTable).filter(FileTable.id == two["id"]).update(
        {FileTable.filename: "../two.txt"}, synchronize_session=False
    )
    db.commit()

    response = client.get(
        f"/files/download-zip?ids={one['id']},{two['id']}", headers=auth(token)
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["one.txt", "two.txt"]

    invalid = client.get("/files/download-zip?ids=abc", headers=auth(token))
    assert invalid.status_code == 400


def test_downloads_do_not_cache_private_content(client):
    token = create_user(client)["access_token"]
    file = upload_one(client, token, "secret.txt", b"secret")
    response = client.get(
        f"/files/download/{file['id']}", headers=auth(token)
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content == b"secret"


def test_stored_path_traversal_is_rejected(client, db):
    token = create_user(client)["access_token"]
    me = client.get("/auth/me", headers=auth(token)).json()

    row = FileTable(
        filename="outside.txt",
        size=3,
        path="../../outside.txt",
        user_id=me["id"],
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    response = client.get(f"/files/download/{row.id}", headers=auth(token))
    assert response.status_code == 404

    # Nothing was written outside the storage root.
    assert not (Path(STORAGE_ROOT).parent / "outside.txt").exists()
