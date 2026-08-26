📦 # StoriX

A full-stack file storage and management platform built with FastAPI and React. StoriX allows users to securely manage files, track storage usage, share files, and access features based on their subscription plan.

🌐 Live Demo: files.ujjawalcodes.site

✨ Features
🔐 User Authentication — Registration and login using JWT-based authentication
📁 File Management — Upload, download, organize, and manage files
📤 File Sharing — Share files publicly or keep them private
💾 Storage Tracking — Monitor storage usage and enforce plan limits
💳 Subscription Plans — Free, Pro, and Premium plans with different storage limits and features
🛡️ Access Control — File ownership checks and plan-based permissions
🔒 Password Security — Passwords are hashed using bcrypt
📱 Responsive Interface — Designed to work across desktop and mobile devices
🛠️ Tech Stack
Area	Technologies
Backend	FastAPI, Python, SQLAlchemy
Frontend	React, Vite, CSS
Database	SQLite
Authentication	JWT, OAuth2, bcrypt
File Storage	Local filesystem with database-backed metadata

---

## 🏗️ How It Works

StoriX separates authentication, file management, subscription plans, and persistent storage into dedicated parts of the application.

```text
React Frontend
      │
      ▼
FastAPI Backend
(Auth • Files • Plans)
      │
      ▼
SQLite Database + Local File Storage
```

### The Basic Flow

1. Users create an account and authenticate to access protected features.
2. Each user is associated with a subscription plan and its storage limits.
3. Files are stored on disk while their metadata and ownership information are stored in the database.
4. Storage limits and ownership checks are enforced during file operations.
5. Files can be kept private or made available for sharing.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.9+
- Node.js 16+
- SQLite (included with Python)

### Backend Setup

```bash
git clone https://github.com/shin0biX/StoriX.git
cd StoriX

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
uvicorn main:app --reload
```

The backend will be available at `http://127.0.0.1:8000`.

### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

---

## 📡 API Overview

| Area | Endpoint | Description |
|---|---|---|
| Authentication | `POST /auth/` | Register a new user |
| Authentication | `POST /auth/token` | Log in and receive an access token |
| Authentication | `GET /auth/me` | Get current user information |
| Files | `POST /files/` | Upload a file |
| Files | `GET /files/` | List user files |
| Files | `DELETE /files/{file_id}` | Delete a file |
| Plans | `GET /plans/` | View available plans |

Interactive API documentation is available locally through FastAPI at `/docs`.

---

## 📁 Project Structure

```text
StoriX/
├── main.py              # FastAPI application entry point
├── models.py            # Database models
├── database.py          # Database configuration
├── routes/
│   ├── auth.py          # Authentication routes
│   ├── files.py         # File management routes
│   └── plans.py         # Subscription plan routes
├── frontend/            # React frontend
├── requirements.txt     # Backend dependencies
└── README.md
```

---

## 🔮 Future Improvements

Some areas I'd like to explore as StoriX evolves:

- ☁️ Cloud storage integration
- 🔗 Expiring and access-controlled sharing links
- 👀 File previews for images, PDFs, and text files
- 🔍 Search and filtering
- 🗑️ Trash/recycle bin and file versioning
- 🔑 Password reset and email verification
- 🐳 Docker support
- 🧪 Automated testing

---

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

<div align="center">

Built with **FastAPI** and **React** 🚀

⭐ If you found this project interesting, consider giving it a star!

</div>
