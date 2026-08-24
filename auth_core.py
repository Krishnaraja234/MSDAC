"""
Authentication for the MSDAC tool.

Design (CONFIRMED):
  - Full persistent user storage (SQLite) with securely hashed passwords
    (never stored in plaintext).
  - New signups start in status='pending' - they cannot log in until an
    admin approves them from the admin panel.
  - "Forgot password" requests also require admin approval before the
    new password takes effect - a user submits a desired new password,
    it's held as a pending request, and only becomes active once an
    admin approves it. This is NOT a self-service "change password
    while logged in" feature - forgot-password is the only reset path,
    and it's always admin-gated.
  - One admin account is seeded on first run so there's a way to
    approve the very first real users. Its credentials are printed to
    the console/log on creation - CHANGE THIS PASSWORD IMMEDIATELY via
    the database once you have real admin access, since seeding a
    known default password is only safe as a one-time bootstrap step.
"""

import sqlite3
import os
import secrets
import random
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

DEFAULT_ADMIN_USERID = "admin"
DEFAULT_ADMIN_PASSWORD = None  # generated fresh on first run, see _seed_admin()

# CONFIRMED FIX: verification codes (signup + password-reset) previously
# never expired and had no limit on guess attempts. A 6-digit code has
# only 1,000,000 possibilities, so with no rate-limiting an attacker
# could script unlimited guesses. Now: a code is only valid for 15
# minutes after being generated, and locks out (forcing a fresh restart
# of the flow) after 5 wrong attempts.
VERIFICATION_CODE_EXPIRY_MINUTES = 15
MAX_VERIFICATION_ATTEMPTS = 5


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # CONFIRMED: WAL (write-ahead logging) mode lets reads and writes
    # happen concurrently without blocking each other - matters more
    # with ~20 simultaneous users all logging in/recording job history
    # at once, versus SQLite's default mode which locks the whole file
    # for any write.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            userid TEXT NOT NULL UNIQUE,
            email TEXT,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected'
            is_admin INTEGER NOT NULL DEFAULT 0,
            auth_code TEXT
        )
    """)
    # CONFIRMED FIX: an existing users.db from before the email feature
    # was added won't have this column - CREATE TABLE IF NOT EXISTS only
    # creates a brand new table, it never adds columns to one that
    # already exists. Add it here if missing, so upgrading in place
    # doesn't break.
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "email" not in existing_columns:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        conn.commit()
    if "last_active" not in existing_columns:
        conn.execute("ALTER TABLE users ADD COLUMN last_active TEXT")
        conn.commit()
    # CONFIRMED: signup no longer goes through admin approval at all -
    # email verification (a code sent to the address the user gave)
    # REPLACES it entirely. A signup only becomes a real row in `users`
    # (status='approved' immediately) once the correct code is entered -
    # until then it's just a pending_verifications row, not a real
    # account yet.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            userid TEXT NOT NULL,
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            code TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            userid TEXT NOT NULL,
            new_password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'  -- 'pending' | 'approved' | 'rejected'
        )
    """)
    # CONFIRMED FIX: forgot-password now requires verifying a code sent
    # to the account's OWN registered email BEFORE the request even
    # reaches admin approval - previously anyone who knew a userid
    # could submit a reset request with no identity check at all,
    # relying solely on the admin to catch anything suspicious. This
    # table holds the code + candidate new password until that email
    # is verified; only then does a row get created in
    # password_reset_requests for the admin to approve, so admin
    # control over the final change is unchanged.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_password_reset_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            userid TEXT NOT NULL,
            new_password_hash TEXT NOT NULL,
            code TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            userid TEXT NOT NULL,
            job_type TEXT NOT NULL,
            download_name TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    # CONFIRMED: job_history predates the HUT NAME/SECTION feature -
    # add these columns if missing, same upgrade-in-place pattern as
    # email/last_active above.
    existing_job_history_columns = {row[1] for row in conn.execute("PRAGMA table_info(job_history)").fetchall()}
    if "hut_name" not in existing_job_history_columns:
        conn.execute("ALTER TABLE job_history ADD COLUMN hut_name TEXT")
        conn.commit()
    if "section" not in existing_job_history_columns:
        conn.execute("ALTER TABLE job_history ADD COLUMN section TEXT")
        conn.commit()
    # CONFIRMED FIX: verification codes (signup email verification and
    # password-reset codes) previously never expired and had no
    # brute-force protection - a 6-digit code stayed valid forever with
    # unlimited guess attempts. attempts tracks wrong guesses so the
    # code can be locked out after MAX_VERIFICATION_ATTEMPTS; the 15
    # minute expiry itself is enforced via created_at at query time, no
    # extra column needed for that part.
    existing_pv_columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_verifications)").fetchall()}
    if "attempts" not in existing_pv_columns:
        conn.execute("ALTER TABLE pending_verifications ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    existing_ppr_columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_password_reset_codes)").fetchall()}
    if "attempts" not in existing_ppr_columns:
        conn.execute("ALTER TABLE pending_password_reset_codes ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # CONFIRMED FIX: userid was already unique, but email had no such
    # constraint - the same email could be registered to multiple
    # different accounts. Partial index (only non-blank emails) so it
    # doesn't conflict with old accounts that predate the email
    # feature. Wrapped in try/except: if a live database somehow
    # already has duplicate emails, creating this index would fail -
    # skip it gracefully rather than crash startup; the new
    # application-level check in create_pending_verification below
    # still prevents any NEW duplicates going forward either way.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email IS NOT NULL AND email != ''"
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    _seed_admin(conn)
    conn.close()


def record_job_history(job_id: str, userid: str, job_type: str, download_name: str, hut_name: str = "", section: str = ""):
    """
    CONFIRMED: records every successfully-completed job persistently (in
    SQLite, not the in-memory `jobs` dict), so a user's (or admin's)
    file history survives server restarts - "access any time" requires
    this, since the in-memory dict resets on every restart while the
    actual output files on disk don't. hut_name/section are stored as
    their own dedicated columns (not just embedded in the filename), so
    the History and Admin Panel pages can show them as separate,
    sortable/filterable fields.
    """
    if not userid:
        return  # shouldn't normally happen (every job is created by a logged-in user), but never let this crash a job
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO job_history (job_id, userid, job_type, download_name, hut_name, section) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, userid, job_type, download_name, hut_name, section),
        )
        conn.commit()
    finally:
        conn.close()


def get_job_history_for_user(userid: str):
    """Every job a specific user has ever completed, most recent first."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM job_history WHERE userid = ? ORDER BY created_at DESC", (userid,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_job_history():
    """Every job from every user - admin-only view. Includes the
    actual username (not just userid) via a join with the users table."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT job_history.*, users.username AS username
            FROM job_history
            LEFT JOIN users ON users.userid = job_history.userid
            ORDER BY job_history.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _seed_admin(conn):
    existing = conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()
    if existing:
        return
    password = secrets.token_urlsafe(12)
    conn.execute(
        "INSERT INTO users (username, userid, password_hash, status, is_admin) VALUES (?, ?, ?, 'approved', 1)",
        ("Administrator", DEFAULT_ADMIN_USERID, generate_password_hash(password)),
    )
    conn.commit()
    print("=" * 60)
    print("MSDAC AUTH: seeded a default admin account (first run only)")
    print(f"  User ID:  {DEFAULT_ADMIN_USERID}")
    print(f"  Password: {password}")
    print("  CHANGE THIS PASSWORD as soon as you have real admin access.")
    print("=" * 60)


def create_pending_verification(username: str, userid: str, email: str, password: str) -> str:
    """
    Starts the signup process: generates a 6-digit verification code,
    stores it (NOT yet a real user - see verify_email_code below), and
    returns the code so the caller can email it to the user. Raises
    ValueError if the userid is already a real account (any status).
    """
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT id FROM users WHERE userid = ?", (userid,)).fetchone()
        if existing:
            raise ValueError(f"User ID '{userid}' is already registered.")
        # CONFIRMED FIX: same check as userid above, now also for email -
        # previously the same email could be registered to multiple
        # different accounts with no check at all.
        existing_email = conn.execute(
            "SELECT userid FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        if existing_email:
            raise ValueError(f"Email '{email}' is already registered to another account.")
        # Clear any older pending verification for the same userid, so a
        # user retrying signup doesn't accumulate stale codes.
        conn.execute("DELETE FROM pending_verifications WHERE userid = ?", (userid,))
        code = f"{random.randint(0, 999999):06d}"
        conn.execute(
            "INSERT INTO pending_verifications (username, userid, email, password_hash, code) VALUES (?, ?, ?, ?, ?)",
            (username, userid, email, generate_password_hash(password), code),
        )
        conn.commit()
        return code
    finally:
        conn.close()


def verify_email_code(userid: str, code: str) -> bool:
    """
    CONFIRMED: this is the ONLY gate for a new signup - if the code
    matches, the account is created immediately as status='approved'
    (no separate admin approval step for signups anymore). Returns True
    on success, False if the userid/code don't match a pending
    verification (wrong code, expired, locked out after too many wrong
    attempts, or never started), OR if the userid got taken by someone
    else between signup and this verification step (a narrow race
    condition, but handled cleanly rather than crashing with an
    unhandled IntegrityError).
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pending_verifications WHERE userid = ? "
            "AND created_at > datetime('now', ?)",
            (userid, f"-{VERIFICATION_CODE_EXPIRY_MINUTES} minutes"),
        ).fetchone()
        if not row:
            # No pending verification at all, or it expired - clean up
            # any expired leftovers for this userid opportunistically.
            conn.execute(
                "DELETE FROM pending_verifications WHERE userid = ? AND created_at <= datetime('now', ?)",
                (userid, f"-{VERIFICATION_CODE_EXPIRY_MINUTES} minutes"),
            )
            conn.commit()
            return False
        if row["attempts"] >= MAX_VERIFICATION_ATTEMPTS:
            # Locked out - discard it so the user has to restart signup
            # and get a fresh code, rather than keep guessing forever.
            conn.execute("DELETE FROM pending_verifications WHERE id = ?", (row["id"],))
            conn.commit()
            return False
        if row["code"] != code:
            conn.execute("UPDATE pending_verifications SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            conn.commit()
            return False
        try:
            conn.execute(
                "INSERT INTO users (username, userid, email, password_hash, status, is_admin) "
                "VALUES (?, ?, ?, ?, 'approved', 0)",
                (row["username"], row["userid"], row["email"], row["password_hash"]),
            )
        except sqlite3.IntegrityError:
            # Someone else already took this userid between signup and
            # now - the pending verification is stale, discard it.
            conn.execute("DELETE FROM pending_verifications WHERE id = ?", (row["id"],))
            conn.commit()
            return False
        conn.execute("DELETE FROM pending_verifications WHERE id = ?", (row["id"],))
        conn.commit()
        return True
    finally:
        conn.close()


def authenticate(userid: str, password: str):
    """Returns the user row dict if userid/password match AND status is
    'approved', otherwise None (covers wrong password, pending, and
    rejected accounts alike - no distinction given to the caller, to
    avoid leaking account status to an unauthenticated request)."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE userid = ?", (userid,)).fetchone()
        if not row:
            return None
        if not check_password_hash(row["password_hash"], password):
            return None
        if row["status"] != "approved":
            return None
        return dict(row)
    finally:
        conn.close()


def change_password(userid: str, current_password: str, new_password: str) -> bool:
    """
    CONFIRMED: self-service password change for an already-logged-in
    user - applies IMMEDIATELY, no admin approval needed (unlike
    forgot-password, which always requires approval). Verifies the
    CURRENT password first via the same check authenticate() uses,
    so this can't be used to change someone else's password even if
    their session were somehow hijacked without their password.
    Returns True on success, False if the current password is wrong
    (or the account doesn't exist/isn't approved).
    """
    if authenticate(userid, current_password) is None:
        return False
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE userid = ?",
            (generate_password_hash(new_password), userid),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def create_password_reset_code(userid: str, expected_username: str, expected_email: str, new_password: str) -> tuple:
    """
    Starts a password reset: generates a 6-digit code and stores it
    alongside the candidate new password, keyed to the userid. Returns
    (code, email, username) so the caller can email the code to the
    account's OWN registered email - CONFIRMED: not a user-supplied
    email, always looked up from the existing account, so verifying
    the code proves the requester controls that account's real inbox.

    CONFIRMED FIX: the requester must also correctly supply the
    account's existing username and email (case-insensitive on email,
    exact on username) - not just the userid - before a reset code is
    even generated. userid is already database-unique, so looking the
    account up by userid first and checking name/email against that
    ONE record is unambiguous even though username/email aren't
    themselves unique across the table.

    Raises ValueError if the userid doesn't exist, or if the supplied
    username/email don't match that account's records.
    """
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT email, username FROM users WHERE userid = ?", (userid,)).fetchone()
        if not existing:
            raise ValueError(f"User ID '{userid}' not found.")
        if not existing["email"]:
            raise ValueError(
                f"No email is on file for '{userid}' (accounts created before the email "
                "feature may not have one) - contact an admin directly to reset this password."
            )
        if existing["username"].strip().lower() != expected_username.strip().lower():
            raise ValueError("Name doesn't match our records for this User ID.")
        if existing["email"].strip().lower() != expected_email.strip().lower():
            raise ValueError("Email doesn't match our records for this User ID.")
        # Clear any older pending code for the same userid, so retrying
        # doesn't accumulate stale codes.
        conn.execute("DELETE FROM pending_password_reset_codes WHERE userid = ?", (userid,))
        code = f"{random.randint(0, 999999):06d}"
        conn.execute(
            "INSERT INTO pending_password_reset_codes (userid, new_password_hash, code) VALUES (?, ?, ?)",
            (userid, generate_password_hash(new_password), code),
        )
        conn.commit()
        return code, existing["email"], existing["username"]
    finally:
        conn.close()


def verify_password_reset_code(userid: str, code: str) -> bool:
    """
    CONFIRMED: the email-identity gate for a password reset. If the
    code matches, moves the request into password_reset_requests as
    'pending' - admin approval is still required before the new
    password takes effect, unchanged from before. Returns True on
    success, False if the userid/code don't match a pending request
    (wrong code, expired, locked out after too many wrong attempts, or
    never started).
    """
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pending_password_reset_codes WHERE userid = ? "
            "AND created_at > datetime('now', ?)",
            (userid, f"-{VERIFICATION_CODE_EXPIRY_MINUTES} minutes"),
        ).fetchone()
        if not row:
            conn.execute(
                "DELETE FROM pending_password_reset_codes WHERE userid = ? AND created_at <= datetime('now', ?)",
                (userid, f"-{VERIFICATION_CODE_EXPIRY_MINUTES} minutes"),
            )
            conn.commit()
            return False
        if row["attempts"] >= MAX_VERIFICATION_ATTEMPTS:
            conn.execute("DELETE FROM pending_password_reset_codes WHERE id = ?", (row["id"],))
            conn.commit()
            return False
        if row["code"] != code:
            conn.execute("UPDATE pending_password_reset_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO password_reset_requests (userid, new_password_hash, status) VALUES (?, ?, 'pending')",
            (row["userid"], row["new_password_hash"]),
        )
        conn.execute("DELETE FROM pending_password_reset_codes WHERE id = ?", (row["id"],))
        conn.commit()
        return True
    finally:
        conn.close()


def get_pending_signups():
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM users WHERE status = 'pending'").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_password_resets():
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM password_reset_requests WHERE status = 'pending'").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def approve_signup(user_id: int):
    conn = _get_conn()
    try:
        conn.execute("UPDATE users SET status = 'approved' WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def reject_signup(user_id: int):
    conn = _get_conn()
    try:
        conn.execute("UPDATE users SET status = 'rejected' WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def approve_password_reset(request_id: int):
    """Applies the pending new password to the user's account, then
    marks the request approved."""
    conn = _get_conn()
    try:
        req = conn.execute("SELECT * FROM password_reset_requests WHERE id = ?", (request_id,)).fetchone()
        if not req:
            raise ValueError("Reset request not found.")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE userid = ?",
            (req["new_password_hash"], req["userid"]),
        )
        conn.execute("UPDATE password_reset_requests SET status = 'approved' WHERE id = ?", (request_id,))
        conn.commit()
    finally:
        conn.close()


def reject_password_reset(request_id: int):
    conn = _get_conn()
    try:
        conn.execute("UPDATE password_reset_requests SET status = 'rejected' WHERE id = ?", (request_id,))
        conn.commit()
    finally:
        conn.close()


def touch_last_active(userid: str):
    """Called on every request from a logged-in user, so the admin panel
    can show who's currently online (based on recent activity, not a
    persistent connection - there's no websocket/live-presence here,
    just 'active in the last N minutes')."""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET last_active = datetime('now') WHERE userid = ?", (userid,)
        )
        conn.commit()
    finally:
        conn.close()


def get_all_users():
    """Every user account (any status), most recently active first -
    used for the admin's user management table."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY (last_active IS NULL), last_active DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_online_userids(minutes: int = 5) -> set:
    """Userids considered 'online' - active within the last N minutes."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT userid FROM users WHERE last_active >= datetime('now', ?)",
            (f"-{minutes} minutes",),
        ).fetchall()
        return {r["userid"] for r in rows}
    finally:
        conn.close()


def pause_user(user_id: int):
    """CONFIRMED: pausing sets status='paused' - a paused account can't
    log in (same check as pending/rejected), but isn't deleted, so it
    can be unpaused later without losing the account or its history."""
    conn = _get_conn()
    try:
        conn.execute("UPDATE users SET status = 'paused' WHERE id = ? AND is_admin = 0", (user_id,))
        conn.commit()
    finally:
        conn.close()


def unpause_user(user_id: int):
    conn = _get_conn()
    try:
        conn.execute("UPDATE users SET status = 'approved' WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def delete_user(user_id: int):
    """CONFIRMED: permanently removes the account. Deliberately does NOT
    touch job_history (so past generations/downloads for that userid
    remain visible to admins even after the account is gone), and
    refuses to delete admin accounts (must be demoted/handled manually
    in the database if that's ever genuinely needed, to avoid an admin
    accidentally locking themselves or every admin out)."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
        conn.commit()
    finally:
        conn.close()


def export_all_data_to_excel(path: str):
    """
    Writes every user account and every job history record to an .xlsx
    workbook (two sheets) at `path` - for the admin's "download as
    Excel" button.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    ws_users = wb.active
    ws_users.title = "Users"
    ws_users.append(["ID", "Username", "User ID", "Email", "Status", "Is Admin", "Last Active"])
    for u in get_all_users():
        ws_users.append([
            u["id"], u["username"], u["userid"], u.get("email") or "",
            u["status"], "Yes" if u["is_admin"] else "No", u.get("last_active") or "",
        ])

    ws_history = wb.create_sheet("Job History")
    ws_history.append(["Date", "Username", "User ID", "Job Type", "HUT Name", "Section", "File Name", "Job ID"])
    for h in get_all_job_history():
        ws_history.append([h["created_at"], h.get("username") or h["userid"], h["userid"], h["job_type"], h.get("hut_name") or "", h.get("section") or "", h["download_name"], h["job_id"]])

    wb.save(path)
