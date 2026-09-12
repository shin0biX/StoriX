# 📦 StoriX

A full-stack file storage and management platform built with **FastAPI** and a **vanilla JS + Tailwind** frontend. StoriX lets users securely manage files, organize them in folders, track storage usage, share files with expiring links, and access features based on their subscription plan.

🌐 **Live Demo:** [files.ujjawalcodes.site](https://files.ujjawalcodes.site/)

---

## ✨ Features

- 🔐 **User Authentication** — JWT-based registration and login, with silent token refresh
- 📁 **File Management** — Upload, download, rename, organize, and manage files
- 📂 **Folders** — Nested folders with breadcrumbs; move files between them
- 📤 **Multi-file Upload** — Drag & drop or browse, with per-file progress and queue UI
- 🔗 **File Sharing** — Public file toggle (plan-gated) and expiring share links
- 🕐 **File Versioning** — Re-uploading a file keeps its previous versions; restore anytime
- ⭐ **Starred / Recent / Shared views** — Quickly reach the files that matter
- 🗑️ **Trash** — Soft delete with restore and "empty trash" (space is freed on permanent delete)
- 👁️ **File Previews** — Inline previews for images, video, audio, PDFs, and text/code
- 🔍 **Search & Sort** — Keyboard-shortcut search (`/`) across all folders, plus sorting and grid/list views
- 📦 **Bulk Actions** — Select multiple files and download them as a ZIP or delete them
- 💾 **Storage Tracking** — Live usage meter and per-type storage breakdown
- 💳 **Subscription Plans** — Free, Pro, and Premium with different limits and sharing rights
- 🛡️ **Admin Console** — User list, storage stats, and plan management for `admin` role users
- 🔒 **Password Security** — Passwords hashed with bcrypt

---

## 🛠️ Tech Stack

| Area | Technologies |
|---|---|
| **Backend** | FastAPI, Python, SQLAlchemy |
| **Frontend** | Vanilla JS, Tailwind (CDN), custom design system |
| **Database** | SQLite |
| **Authentication** | JWT (OAuth2 password flow), bcrypt |
| **File Storage** | Local filesystem with database-backed metadata |

---

## 🏗️ How It Works

```text
Static Frontend (HTML/JS, served by FastAPI)
      │
      ▼
FastAPI Backend
(Auth • Files • Folders • Plans • Admin)
      │
      ▼
SQLite Database + Local File Storage (storage/<username>/)
```

1. Users create an account and authenticate to access the workspace.
2. Each user belongs to a subscription plan with storage/per-file limits and sharing rights.
3. Files are stored on disk while metadata (ownership, folder, version, trash state) lives in the database.
4. Limits and ownership are enforced during every file operation.
5. Files can be kept private, marked public, or shared via signed expiring links.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+

### Backend + Frontend

```bash
git clone https://github.com/shin0biX/StoriX.git
cd StoriX

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
uvicorn main:app --reload
```

The app is served at `http://127.0.0.1:8000` — landing page, auth, and workspace included.

### Docker

```bash
docker compose up --build
```

### Admin Access

Promote an existing user to admin:

```bash
python make_admin.py <username>
```

Then open `/admin.html` from the sidebar link.

---

## 📡 API Overview

| Area | Endpoint | Description |
|---|---|---|
| Auth | `POST /auth/` | Register a new user |
| Auth | `POST /auth/token` | Log in and receive an access token |
| Auth | `POST /auth/refresh` | Renew the access token |
| Auth | `GET /auth/me` | Current user + plan info |
| Files | `POST /files/upload/` | Upload one or more files (`files`, optional `folder_id`) |
| Files | `GET /files/get-files` | List active files (latest version each) |
| Files | `PUT /files/{id}/rename` · `/move` · `/star` · `/{id}/visibility` | File management |
| Files | `GET /files/preview/{id}` | Inline preview with correct MIME type |
| Files | `GET /files/versions/{id}` · `POST /files/versions/{id}/restore/{vid}` | Version history |
| Files | `DELETE /files/file-delete/{id}` | Move to trash |
| Files | `GET /files/trash` · `POST /files/restore/{id}` · `DELETE /files/permanent-delete/{id}` · `POST /files/trash/empty` | Trash management |
| Files | `GET /files/folders` · `POST /files/folders` · `PUT /files/folders/{id}/rename` · `DELETE /files/folders/{id}` | Folders |
| Files | `GET /files/download-zip?ids=1,2,3` | Bulk ZIP download |
| Files | `POST /files/share/{id}?expires_minutes=30` | Create expiring share link |
| Plans | `PUT /plans/change-plan` | Switch subscription plan |
| Admin | `GET /admin/users` · `PUT /admin/users/{id}/plan` · `DELETE /admin/users/{id}` | Admin console |

Interactive API documentation is available locally through FastAPI at `/docs`.

---

## 📁 Project Structure

```text
StoriX/
├── main.py              # FastAPI entry point, migrations, plan seeding
├── models.py            # User, FileTable, Folder, Plan models
├── database.py          # Database configuration
├── make_admin.py        # Promote a user to admin
├── routes/
│   ├── auth.py          # Authentication + token refresh
│   ├── files.py         # Upload, folders, versions, trash, sharing, previews, ZIP
│   ├── plans.py         # Plan switching
│   └── admin.py         # Admin console API
├── frontend/
│   ├── index.html       # Landing page
│   ├── auth.html        # Login / register
│   ├── dashboard.html   # Workspace (files, folders, trash, search, upload)
│   ├── admin.html       # Admin console
│   ├── shared.html      # Public share-link download page
│   ├── css/app.css      # Design system
│   └── js/              # api.js, ui.js, auth.js, dashboard.js, admin.js
├── requirements.txt
├── Dockerfile / docker-compose.yml
└── README.md
```

---

## 🔮 Future Improvements

- ☁️ Cloud storage integration (S3 adapter)
- 👀 Thumbnail generation for image grid view
- 🔑 Password reset and email verification
- 💳 Real payments for plan upgrades (e.g. Stripe)
- 🧪 Automated tests

---

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

<div align="center">

Built with **FastAPI** 🚀

⭐ If you found this project interesting, consider giving it a star!

</div>
