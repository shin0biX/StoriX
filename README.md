# StoriX

A full-stack file storage and management platform with user authentication, subscription plans, and file sharing capabilities built with FastAPI and React.

## Features

- **User Authentication**: Secure registration, login, and session management with JWT tokens
- **Subscription Plans**: Tiered storage plans (Free, Pro, Premium) with different limits and features
- **File Management**: Upload, download, organize, and share files with granular permissions
- **Role-Based Access Control**: Different permissions based on user roles and subscription plans
- **Storage Tracking**: Monitor used vs available storage per user
- **File Sharing**: Option to make files publicly shareable or keep private
- **Responsive Design**: Works on mobile and desktop devices
- **RESTful API**: Well-documented endpoints for all operations
- **Password Hashing**: Secure password storage using bcrypt
- **Plan Seeding**: Automatic creation of default subscription plans on startup

## Technology Stack

### Backend
- **Framework**: FastAPI (modern, fast web framework for Python)
- **Language**: Python 3.9+
- **Database**: SQLAlchemy ORM with SQLite
- **Authentication**: 
  - JWT (JSON Web Tokens) for session management
  - bcrypt for password hashing
  - OAuth2 password flow for token generation
- **Authorization**: Role-based access control with plan-based feature gating
- **File Storage**: 
  - Database storage of file metadata
  - Configurable upload directory
  - File size and storage limit enforcement
- **API Design**: RESTful endpoints with proper HTTP status codes and validation

### Frontend
- **Framework**: React (via Vite)
- **Build Tool**: Vite (fast development server and build tool)
- **Styling**: CSS (with potential for extension)
- **State Management**: React hooks and context
- **Routing**: Client-side routing for different views
- **File Handling**: Upload/download components with progress indicators

### DevOps & Infrastructure
- **Dependency Management**: `requirements.txt` (Python) and `package.json` (Node.js)
- **Environment Agnostic**: Works with local development and production deployment
- **Database Migration**: Automatic table creation and plan seeding on startup
- **Static File Serving**: Built-in frontend serving for simplified deployment
- **CORS Configuration**: Development-friendly CORS settings

## API Endpoints

### Authentication
- `POST /auth/` - Register a new user
  - Parameters: `username`, `email`, `full_name`, `password`, `plan_id`
  - Returns: Success message with user ID
- `POST /auth/token` - Login and get access token
  - Parameters: `username`, `password` (OAuth2 form)
  - Returns: `access_token` and `token_type`
- `GET /auth/me` - Get current user information
  - Returns: User details, storage usage, plan information

### Files
- `POST /files/` - Upload a new file
  - Parameters: File data, metadata
  - Returns: File information
- `GET /files/` - List user's files
  - Returns: Array of file objects with metadata
- `GET /files/{file_id}` - Get specific file details
- `DELETE /files/{file_id}` - Delete a file
- `PUT /files/{file_id}` - Update file metadata (name, public status)

### Plans
- `GET /plans/` - List available subscription plans
  - Returns: Array of plan objects with limits and pricing
- `GET /plans/{plan_id}` - Get specific plan details

## Installation & Setup

### Prerequisites
- Python 3.9+
- Node.js 16+ (for frontend)
- SQLite (included with Python)

### Backend Setup
```bash
# Clone the repository
git clone https://github.com/shin0biX/StoriX.git
cd StoriX

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn main:app --reload
```

### Frontend Setup
```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev

# For production build
npm run build
```

## How It Works

### User Flow
1. **Registration**: New users sign up with email, username, password, and select a plan
2. **Login**: Users authenticate via OAuth2 password flow to receive JWT token
3. **File Operations**: Authenticated users can upload, view, download, and manage files
4. **Storage Management**: System tracks used storage against plan limits
5. **File Sharing**: Users can designate files as public for sharing

### Authentication Process
- Passwords are hashed using bcrypt before storage
- Successful login returns JWT token signed with secret key
- Token includes user ID, username, role, and expiration
- Protected endpoints validate token via Authorization header
- Token validation checks signature, expiration, and extracts user info

### Plan System
- Three default plans seeded on startup: Free, Pro, Premium
- Each plan has:
  - Storage limit (total bytes allowed)
  - Max file size (largest individual file allowed)
  - Sharing capability (boolean)
  - Price (monthly in arbitrary units)
- Users are assigned a plan during registration
- Plan limits enforced during file uploads

### File Storage
- Files stored in configured upload directory
- Database stores metadata: filename, size, path, timestamps, ownership
- Public files can be accessed via shareable links
- Private files require authentication to access
- Storage usage tracked per user and enforced against plan limits

## Security Features

### Password Security
- Passwords hashed using bcrypt with salt
- No plaintext passwords ever stored
- Hash verification during login

### Token Security
- JWT tokens signed with HS256 algorithm
- Secret key stored in source (should be moved to environment variables in production)
- Token expiration (20 minutes by default)
- Protection against common JWT attacks

### Authorization
- Role-based access control (currently implements 'user' role)
- Plan-based feature gating (storage limits, sharing capabilities)
- Ownership verification for file operations
- Authentication required for all protected endpoints

### Input Validation
- Pydantic models validate all incoming data
- Email format validation
- Password strength could be enhanced
- File type and size validation during upload

## Project Structure

```
StoriX/
├── main.py                 # FastAPI application entry point
├── models.py               # SQLAlchemy data models (User, FileTable, Plan)
├── database.py             # Database setup and session management
├── requirements.txt        # Python dependencies
├── routes/
│   ├── auth.py             # Authentication routes (register, login, me)
│   ├── files.py            # File management routes (upload, list, delete)
│   └── plans.py            # Subscription plan routes
├── frontend/
│   ├── src/                # React source code
│   │   ├── components/     # Reusable UI components
│   │   ├── pages/          # Page components (Login, Dashboard, Files, etc.)
│   │   ├── App.jsx         # Main application component
│   │   └── main.jsx        # React entry point
│   ├── public/             # Static assets
│   ├── package.json        # Node.js dependencies and scripts
│   └── vite.config.js      # Vite configuration
├── hosting.db              # SQLite database file
└── .gitignore              # Git ignore rules
```

## Design Decisions

### Why FastAPI?
- Automatic API documentation (OpenAPI/Swagger)
- Built-in data validation with Pydantic
- High performance (async capabilities)
- Excellent developer experience
- Modern Python features (type hints, async/await)

### Why JWT for Authentication?
- Stateless authentication suitable for REST APIs
- Token contains user information reducing database lookups
- Standardized format with libraries available in many languages
- Easy to implement with expiration and refresh capabilities

### Why bcrypt for Passwords?
- Industry standard for password hashing
- Configurable work factor to resist brute-force attacks
- Includes salt to prevent rainbow table attacks
- Widely tested and trusted

### Why Separate Routers?
- Clean separation of concerns (auth vs files vs plans)
- Easier maintenance and testing
- Logical grouping of related endpoints
- Scalable architecture for adding new features

### Why Seed Plans Automatically?
- Ensures default plans always exist
- Simplifies initial setup for new installations
- Prevents errors from missing plan references
- Consistent experience across deployments

## Limitations & Future Improvements

### Current Limitations
- Secret key hardcoded in source (should use environment variables)
- Single instance deployment (no built-in clustering)
- Limited frontend implementation (basic structure present)
- No file preview or in-browser viewing capabilities
- Limited administrative controls
- No password reset or email verification functionality
- Basic role system (only 'user' role implemented)
- No audit logging for security or compliance
- File storage limited to local disk (no cloud storage options)

### Planned Enhancements
- Environment variable configuration for secrets
- Docker containerization for easy deployment
- Enhanced frontend with complete UI implementation
- File preview capabilities (images, PDFs, text files)
- Administrative dashboard for managing users and plans
- Password reset and email verification workflows
- Enhanced role system (admin, moderator, etc.)
- Audit logging for security and compliance
- Cloud storage integration (AWS S3, Google Cloud Storage)
- File versioning and trash/recycle bin functionality
- Sharing links with expiration and access controls
- Activity feed and notifications
- Search and filtering capabilities for files
- Storage analytics and usage reports
- Webhook integration for third-party services

## Contributing

StoriX is open to contributions! If you'd like to improve this project:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Commit your changes (`git commit -m 'Add some amazing-feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

Please ensure your contributions align with the project's coding standards and include appropriate tests.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Inspired by file storage and sharing platforms like Dropbox, Google Drive, and WeTransfer
- Built with the excellent FastAPI framework
- Utilizes React and Vite for modern frontend development
- Thanks to the open-source community for the dependencies that make this possible