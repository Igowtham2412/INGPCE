"""Reset an existing admin password without putting it in shell history."""
import asyncio
import getpass
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import auth, db


async def main(email: str) -> None:
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise SystemExit("Provide the administrator account email address.")
    password = getpass.getpass("New password (16+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 16:
        raise SystemExit("Password must be at least 16 characters.")
    if password != confirmation:
        raise SystemExit("Passwords did not match.")

    await db.init_pool()
    try:
        user = await db.get_user_by_login(email.strip())
        if user is None or user["role"] != "admin" or not user["is_active"]:
            raise SystemExit("No active administrator account found for that email.")
        updated = await db.update_user_password(
            int(user["id"]), auth.hash_password(password),
        )
        if not updated:
            raise RuntimeError("Administrator account was not updated.")
        await db.write_audit_log(
            "admin_password_reset", int(user["id"]), user["email"],
            target_type="user", target_id=str(user["id"]),
            details={"sessions_revoked": True},
        )
        print(f"Password reset for {user['email']}; previous sessions were revoked.")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/reset_admin_password.py admin@example.com")
    asyncio.run(main(sys.argv[1]))
