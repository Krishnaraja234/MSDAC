"""
Run this on your server, in the msdac_app folder, to reset the admin
account's password directly - use this if you missed/lost the
auto-generated password from the very first server start.

Usage:
    python reset_admin_password.py YourNewPasswordHere
"""
import sys
import sqlite3
from werkzeug.security import generate_password_hash

if len(sys.argv) != 2:
    print("Usage: python reset_admin_password.py <new_password>")
    sys.exit(1)

new_password = sys.argv[1]
conn = sqlite3.connect("users.db")
cur = conn.execute("SELECT userid FROM users WHERE is_admin = 1")
admin = cur.fetchone()
if not admin:
    print("No admin account found in users.db")
    sys.exit(1)

conn.execute(
    "UPDATE users SET password_hash = ? WHERE is_admin = 1",
    (generate_password_hash(new_password),),
)
conn.commit()
conn.close()
print(f"Admin account '{admin[0]}' password reset successfully.")
