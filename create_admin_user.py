"""
Run this on your server, in the msdac_app folder, to directly create a
new user with full admin access - bypasses the normal signup/email
verification flow entirely, since this is meant for you to set up
accounts yourself.

Usage:
    python create_admin_user.py <username> <userid> <email> <password>

Example:
    python create_admin_user.py "Krish" "Usts_275" "someone@example.com" "YourChosenPassword"
"""
import sys
import sqlite3
from werkzeug.security import generate_password_hash

if len(sys.argv) != 5:
    print('Usage: python create_admin_user.py <username> <userid> <email> <password>')
    sys.exit(1)

username, userid, email, password = sys.argv[1:5]

conn = sqlite3.connect("users.db")
existing = conn.execute("SELECT id FROM users WHERE userid = ?", (userid,)).fetchone()
if existing:
    print(f"User ID '{userid}' already exists. Use reset_admin_password.py-style logic to change its password instead, or pick a different User ID.")
    sys.exit(1)

conn.execute(
    "INSERT INTO users (username, userid, email, password_hash, status, is_admin) VALUES (?, ?, ?, ?, 'approved', 1)",
    (username, userid, email, generate_password_hash(password)),
)
conn.commit()
conn.close()
print(f"Admin user '{userid}' created successfully with full admin access.")
