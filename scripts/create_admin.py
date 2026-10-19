"""Create the first administrator without putting a password in command history."""
import getpass
import os
import re
import sys
import asyncio

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import auth, db


async def main(email: str) -> None:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise SystemExit("Provide a valid administrator email address.")
    password = getpass.getpass("New administrator password (12+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters.")
    if password != confirmation:
        raise SystemExit("Passwords did not match.")
    await db.init_pool()
    try:
        await db.create_user(email, auth.hash_password(password), "admin")
        await db.write_audit_log(
            "admin_created", actor_email=email.lower(),
            target_type="user", target_id=email.lower(),
        )
        print(f"Administrator account created for {email.lower()}.")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/create_admin.py admin@example.com")
    asyncio.run(main(sys.argv[1]))
