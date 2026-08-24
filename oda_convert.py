"""
Wrapper around ODA File Converter for DXF -> DWG conversion.

ODA File Converter is invoked PER FOLDER (not per file) - this is the
documented-safe pattern for MSDAC: give it an input folder and an output
folder, and it converts every DXF inside. Each generation job gets its
own UUID-named folder pair so concurrent jobs from different engineers
never share files and can't clash.

CONFIG: point ODA_EXE_PATH at your ODAFileConverter.exe. Default assumes
it's sitting right next to this script - change this one line if you
move it elsewhere later.
"""

import os
import shutil
import subprocess
import sys

# ---- CONFIG: change this if you move the ODA executable ----
# CONFIRMED: resolved relative to THIS file's own folder (msdac_app/),
# not hardcoded to one machine's absolute path - so the whole app folder
# can be copied/moved anywhere and this still finds
# "ODAFileConverter 25.12.0/ODAFileConverter.exe" sitting right next to it.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
ODA_EXE_PATH = os.path.join(_APP_DIR, "ODAFileConverter", "ODAFileConverter_QT6_lnxX64_8.3dll_27.1.AppImage")
# --------------------------------------------------------------

# ODA File Converter CLI signature:
#   ODAFileConverter <in_folder> <out_folder> <out_version> <out_type> <recurse> <audit> [filter]
# out_version: e.g. "ACAD2013"   out_type: "DWG" or "DXF"
ODA_OUTPUT_VERSION = "ACAD2013"
ODA_RECURSE = "0"
ODA_AUDIT = "1"


class OdaConversionError(RuntimeError):
    pass


def _run_oda(input_folder: str, output_folder: str, out_type: str, timeout: int) -> None:
    if not os.path.isfile(ODA_EXE_PATH):
        raise OdaConversionError(
            f"ODA File Converter not found at '{ODA_EXE_PATH}'. "
            "Place ODAFileConverter.exe next to signal_core.py / app.py, "
            "or update ODA_EXE_PATH in oda_convert.py."
        )

    os.makedirs(output_folder, exist_ok=True)

    cmd = [
        ODA_EXE_PATH,
        input_folder,
        output_folder,
        ODA_OUTPUT_VERSION,
        out_type,
        ODA_RECURSE,
        ODA_AUDIT,
    ]

    # On Windows, hide ODA File Converter's own window so it doesn't
    # flash visibly on screen during conversion - it still runs normally,
    # just silently in the background.
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
