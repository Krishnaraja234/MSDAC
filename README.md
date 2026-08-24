# MSDAC Signal Circuit Generator

Flask app that turns the `SIGNAL` sheet of a MSDAC workbook into a full set
of DWG circuit drawings, using your existing typical templates and a
separately-uploaded border/title-block template.

## Setup

```
pip install -r requirements.txt
```

Place `ODAFileConverter.exe` in this same folder (next to `app.py`). If you
keep it somewhere else, update `ODA_EXE_PATH` at the top of `oda_convert.py`.

## Running

Development (single-threaded, fine for testing):
```
python app.py
```

Production / real concurrent use (multiple engineers at once):
```
pip install waitress
waitress-serve --host=0.0.0.0 --port=5000 app:app
```
Flask's built-in dev server should NOT be used for real concurrent jobs -
it's single-threaded by default and will queue engineers' requests behind
each other. Waitress (or gunicorn) plus the ThreadPoolExecutor job queue
already wired into `app.py` is what makes concurrent generation safe.

## Folder structure

```
app.py              Flask routes: upload, status polling, download
signal_core.py       Core placeholder-substitution logic (the actual "brain")
oda_convert.py       ODA File Converter wrapper (DXF<->DWG, per-folder + per-file)
dxf_templates/       The 10 signal typical templates (HZR_HHZR_DZR, STANDARD1-4, etc.)
templates/index.html Frontend page (dark theme matching your existing MSDAC pages)
jobs/                Runtime: one UUID folder per generation job (auto-created/cleaned)
uploads/             Runtime scratch space
```

## How generation works

1. User uploads their MSDAC workbook (must contain `SIGNAL` and `FIELD PG.NO` sheets)
   and a border/title-block template (`.dxf` or `.dwg`).
2. The app reads every row of `SIGNAL` (columns: SIG NAME, DIRECTION, LOC,
   TYPICAL, LCPR, AHEAD SIG).
3. Starting sheet number comes from `FIELD PG.NO!C6` (the Sheet Number
   value on the SIGNAL row of the index sheet) - NOT hardcoded.
4. For each row, `TYPICAL` determines which typical template file(s) get
   used (see `TYPICAL_SHEET_SETS` in `signal_core.py`). If `LCPR` is
   filled, sheets 3 and 4 swap for their `_LCPR` variant.
5. Placeholder substitution per sheet:
   - `*` token -> SIG NAME
   - `$` token -> AHEAD SIG
   - `@` token -> LCPR (only on LCPR/AHPR relay blocks)
   - literal text `"LOCATION"` -> LOC
   - `#` inside FUZEBLOCK/FUZEENDBLOCK `VOLT` attribute -> DIRECTION
6. The uploaded border/title-block template's blocks (e.g. `TEMPHUT1`,
   `TITLE`) are imported and inserted at `(0,0)` into every generated
   sheet. The `TITLE` block's `SHT`/`CONT` attributes get the running
   sheet number / next sheet number; its `TITLE` attribute text gets the
   same `*`/`$` substitution as everywhere else.
7. All generated DXF sheets are converted to DWG via ODA File Converter
   (one job = one temp folder, so concurrent engineers' jobs never share
   files) and zipped for download.

## Known assumptions to double check against real production data

- `TYPICAL_SHEET_SETS` and `LCPR_SWAP` in `signal_core.py` only cover the
  10 template files reviewed so far (HZR+HHZR+DZR, STANDARD, STANDARD+ZRP).
  If other TYPICAL values exist in real workbooks, add them here.
- The border/title-block importer inserts every block referenced by an
  INSERT in the border template's modelspace, at whatever insertion point
  that INSERT had (normally `(0,0)`). If your real border template has a
  different structure, this should still work, but worth a first-run check.
