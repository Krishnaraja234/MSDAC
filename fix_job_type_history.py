"""
One-time cleanup script: fixes existing job_history rows whose job_type
column holds the old mangled-filename text (e.g. "Hut2 vn-kdy 20260729
205312 signal circuits") instead of a clean type label (e.g. "SIGNAL").

Run this ONCE on the server, from the same folder as users.db:
    python fix_job_type_history.py

Safe to run more than once - rows that already have a clean job_type
are left untouched.
"""
import auth_core

# CONFIRMED: same distinctive filename substrings used when building
# each job's download_name (see _build_download_zip_name calls in
# app.py) - matched against download_name to recover the correct type
# for rows whose job_type was mangled by the old bug.
PATTERNS = [
    ("signal_circuits.zip", "SIGNAL"),
    ("track_circuits.zip", "TRACK"),
    ("ar_azr_circuits.zip", "AR & AZR"),
    ("relay_rack.zip", "RELAY RACK"),
    ("communication_circuits.zip", "COMMUNICATION"),
    ("sdf_circuits.zip", "SDF"),
    ("datalogger_circuits.zip", "DATA LOGGER"),
    ("custom_circuits.zip", "CUSTOM CIRCUITS"),
    ("full_ifc.zip", "FULL IFC"),
    ("retemplated_output.zip", "RE-TEMPLATE DRAWINGS"),
]

KNOWN_CLEAN_TYPES = {label for _pattern, label in PATTERNS}


def main():
    conn = auth_core._get_conn()
    rows = conn.execute("SELECT job_id, job_type, download_name FROM job_history").fetchall()

    fixed = 0
    skipped_already_clean = 0
    skipped_no_match = 0

    for row in rows:
        job_id = row["job_id"]
        current_type = (row["job_type"] or "").strip()
        download_name = row["download_name"] or ""

        if current_type.upper() in KNOWN_CLEAN_TYPES:
            skipped_already_clean += 1
            continue

        matched_label = None
        for pattern, label in PATTERNS:
            if pattern in download_name:
                matched_label = label
                break

        if matched_label is None:
            print(f"  No match for job_id={job_id!r} download_name={download_name!r} - left as-is")
            skipped_no_match += 1
            continue

        conn.execute(
            "UPDATE job_history SET job_type = ? WHERE job_id = ?",
            (matched_label, job_id),
        )
        fixed += 1

    conn.commit()
    conn.close()

    print(f"\nFixed {fixed} row(s).")
    print(f"Already clean: {skipped_already_clean} row(s).")
    print(f"Could not match (left as-is): {skipped_no_match} row(s).")


if __name__ == "__main__":
    main()
