"""Promote a user to admin:  python make_admin.py <username>"""
import sys
from database import SessionLocal
from models import User

def main():
    if len(sys.argv) < 2:
        print("Usage: python make_admin.py <username>")
        sys.exit(1)
    username = sys.argv[1]
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            print(f"User '{username}' not found")
            sys.exit(1)
        user.role = 'admin'
        db.commit()
        print(f"'{username}' is now an admin")
    finally:
        db.close()

if __name__ == "__main__":
    main()
