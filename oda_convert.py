"""
Wrapper around ODA File Converter for DXF -> DWG conversion.

ODA File Converter is invoked PER FOLDER (not per file) - this is the
documented-safe pattern for MSDAC: give it an input folder and an output
folder, and it converts every DXF inside. Each generation job gets its
own UUID-named folder pair so concurrent jobs from different engineers
never share files and can't clash.

CONFIG (cross-platform):
  - On Windows: looks for "ODAFileConverter <version>/ODAFileConverter.exe"
    next to this script, same as before. Set ODA_EXE_PATH env var to
    override.
  - On Linux (e.g. Render Docker deploy): looks for the "ODAFileConverter"
    command installed via the .deb/.rpm package (normally lands on PATH
    at /usr/bin/ODAFileConverter), or an AppImage path via the
    ODA_EXE_PATH env var. ODA's Linux build is a GUI app, so it must be
    run under a virtual display (xvfb) - see the accompanying Dockerfile,
    which launches the whole app with `xvfb-run`.
"""

import os
import shutil
import subprocess
import sys

# ---- CONFIG ----
# Explicit override always wins, on any OS: set ODA_EXE_PATH to the full
# path of the exe/binary/AppImage if it's not in a location this script
# already knows to check.
_ODA_EXE_PATH_OVERRIDE = os.environ.get("ODA_EXE_PATH")

_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_oda_exe_windows() -> str | None:
    # Same behavior as before: look for the bundled folder next to this
    # file. Version-named subfolder may change between ODA releases, so
    # this scans for any "ODAFileConverter*" folder containing the exe.
    if not os.path.isdir(_APP_DIR):
        return None
    for entry in os.listdir(_APP_DIR):
        candidate = os.path.join(_APP_DIR, entry, "ODAFileConverter.exe")
        if entry.lower().startswith("odafileconverter") and os.path.isfile(candidate):
            return candidate
    # Fall back to the old hardcoded path in case the folder name matches
    # exactly what was there before.
    legacy = os.path.join(_APP_DIR, "ODAFileConverter 25.12.0", "ODAFileConverter.exe")
    return legacy if os.path.isfile(legacy) else None


def _find_oda_exe_linux() -> str | None:
    # 1. Installed .deb/.rpm package normally puts the binary on PATH.
    on_path = shutil.which("ODAFileConverter")
    if on_path:
        return on_path

    # 2. Common install locations if it's not on PATH for some reason.
    common_paths = [
        "/usr/bin/ODAFileConverter",
        "/usr/local/bin/ODAFileConverter",
        "/opt/ODAFileConverter/ODAFileConverter",
    ]
    for path in common_paths:
        if os.path.isfile(path):
            return path

    # 3. An AppImage bundled directly into the app folder (if that's the
    # approach used instead of installing a .deb/.rpm system-wide).
    for entry in os.listdir(_APP_DIR) if os.path.isdir(_APP_DIR) else []:
        if entry.lower().endswith(".appimage") and "odafileconverter" in entry.lower():
            return os.path.join(_APP_DIR, entry)

    return None


def _resolve_oda_exe_path() -> str | None:
    if _ODA_EXE_PATH_OVERRIDE:
        return _ODA_EXE_PATH_OVERRIDE
    if sys.platform == "win32":
        return _find_oda_exe_windows()
    return _find_oda_exe_linux()


ODA_EXE_PATH = _resolve_oda_exe_path()

# ODA File Converter CLI signature:
#   ODAFileConverter <in_folder> <out_folder> <out_version> <out_type> <recurse> <audit> [filter]
# out_version: e.g. "ACAD2013"   out_type: "DWG" or "DXF"
ODA_OUTPUT_VERSION = "ACAD2013"
ODA_RECURSE = "0"
ODA_AUDIT = "1"


class OdaConversionError(RuntimeError):
    pass


def _run_oda(input_folder: str, output_folder: str, out_type: str, timeout: int) -> None:
    exe_path = ODA_EXE_PATH or _resolve_oda_exe_path()
    if not exe_path or not os.path.isfile(exe_path):
        if sys.platform == "win32":
            raise OdaConversionError(
                "ODA File Converter not found. Place ODAFileConverter.exe in a "
                "folder named 'ODAFileConverter <version>' next to app.py, "
                "or set the ODA_EXE_PATH environment variable to its full path."
            )
        raise OdaConversionError(
            "ODA File Converter not found on this Linux host. Install the "
            "Linux .deb/.rpm build from opendesign.com (see Dockerfile), or "
            "set ODA_EXE_PATH to an AppImage/binary path. Also confirm the "
            "app is being launched under xvfb-run, since ODA's Linux build "
            "requires a display even when run headlessly."
        )

    os.makedirs(output_folder, exist_ok=True)

    cmd = [
        exe_path,
        input_folder,
        output_folder,
        ODA_OUTPUT_VERSION,
        out_type,
        ODA_RECURSE,
        ODA_AUDIT,
    ]

    # On Windows, hide ODA File Converter's own window so it doesn't
    # flash visibly on screen during conversion - it still runs normally,
    # just silently in the background. Not applicable/needed on Linux.
    creationflags = 0
    startupinfo = None
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    except subprocess.TimeoutExpired as exc:
        raise OdaConversionError(f"ODA File Converter timed out after {timeout}s") from exc
    except PermissionError as exc:
        raise OdaConversionError(
            f"Permission denied running '{exe_path}'. On Linux, the binary "
            "needs execute permission (chmod +x) - this is normally handled "
            "automatically by installing the .deb/.rpm package rather than "
            "just copying the file."
        ) from exc

    if result.returncode != 0:
        raise OdaConversionError(
            f"ODA File Converter exited with code {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    ext = out_type.lower()
    produced = [f for f in os.listdir(output_folder) if f.lower().endswith(f".{ext}")]
    if not produced:
        raise OdaConversionError(
            f"ODA File Converter ran but produced no .{ext} files. "
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def convert_folder_to_dwg(input_folder: str, output_folder: str, timeout: int = 120) -> None:
    """Convert every DXF in input_folder to DWG, writing results into output_folder."""
    _run_oda(input_folder, output_folder, "DWG", timeout)


def convert_folder_to_dxf(input_folder: str, output_folder: str, timeout: int = 120) -> None:
    """Convert every DWG in input_folder to DXF, writing results into output_folder."""
    _run_oda(input_folder, output_folder, "DXF", timeout)


def convert_single_file_to_dxf(input_path: str, timeout: int = 60) -> str:
    """
    Convenience wrapper: convert a single .dwg file to .dxf, using isolated
    temp in/out folders so it's safe under concurrent use. Returns the path
    to the resulting .dxf file.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_in, tempfile.TemporaryDirectory() as tmp_out:
        in_name = os.path.basename(input_path)
        tmp_in_path = os.path.join(tmp_in, in_name)
        shutil.copy(input_path, tmp_in_path)

        convert_folder_to_dxf(tmp_in, tmp_out, timeout=timeout)

        produced = [f for f in os.listdir(tmp_out) if f.lower().endswith(".dxf")]
        if not produced:
            raise OdaConversionError(f"No DXF produced for {input_path}")

        result_path = input_path.rsplit(".", 1)[0] + "_converted.dxf"
        shutil.move(os.path.join(tmp_out, produced[0]), result_path)
        return result_path
