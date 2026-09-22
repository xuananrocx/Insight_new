"""Local-only administrator recovery: python -m src.core.manage_accounts USERNAME."""

import argparse
from getpass import getpass
from src.core import accounts as a
from src.db import metadata_db


def main():
    parser = argparse.ArgumentParser(
        description="Reset an account password locally on the server"
    )
    parser.add_argument("username")
    args = parser.parse_args()
    metadata_db.init_db()
    a.init_auth()
    users = a.rows("SELECT id FROM auth_users WHERE username=?", (args.username,))
    if not users:
        parser.error("Account does not exist")
    password = getpass("New temporary password (12-128 characters): ")
    if password != getpass("Confirm password: "):
        parser.error("Passwords do not match")
    a.validate_password(password)
    uid = users[0]["id"]
    with metadata_db.get_cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        cur.execute(
            "UPDATE auth_users SET password_hash=?,must_change_password=1 WHERE id=?",
            (a.passwords.hash(password), uid),
        )
        cur.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
        cur.connection.commit()
    a.audit("local_password_reset", uid)
    print(
        "Password reset. Existing sessions were revoked. The user must change it at next login."
    )


if __name__ == "__main__":
    main()
