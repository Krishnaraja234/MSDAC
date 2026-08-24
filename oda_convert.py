"""
Wrapper around ODA File Converter for DXF <-> DWG conversion.

The Linux ODA File Converter AppImage is extracted into:

    ODAFileConverter/
        squashfs-root/
            AppRun
            ...

We execute AppRun directly instead of executing the AppImage, which avoids
the FUSE requirement on Render.

The extracted ODA libraries are added to LD_LIBRARY_PATH so libraries such
as libTD_Db.so can be found correctly.
"""

import os
import shutil
import subprocess
import sys
import tempfile


# ---------------------------------------------------------------------------
# ODA CONFIGURATION
# ---------------------------------------------------------------------------

_APP_DIR = os.path.dirname(os.path.abspath(__file__))

ODA_ROOT = os.path.join(
    _APP_DIR,
    "ODAFileConverter",
    "squashfs-root",
)

ODA_EXE_PATH = os.path.join(
    ODA_ROOT,
    "AppRun",
)

ODA_OUTPUT_VERSION = "ACAD2013"
ODA_RECURSE = "0"
ODA_AUDIT = "1"


# ---------------------------------------------------------------------------
# ERROR
# ---------------------------------------------------------------------------

class OdaConversionError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# VALIDATE ODA INSTALLATION
# ---------------------------------------------------------------------------

def _validate_oda() -> None:
    if not os.path.isdir(ODA_ROOT):
        raise OdaConversionError(
            f"ODA extracted directory not found at '{ODA_ROOT}'. "
            "Make sure ODAFileConverter/squashfs-root exists."
        )

    if not os.path.isfile(ODA_EXE_PATH):
        raise OdaConversionError(
            f"ODA AppRun not found at '{ODA_EXE_PATH}'. "
            "Make sure the AppImage was extracted into "
            "ODAFileConverter/squashfs-root."
        )

    if not os.access(ODA_EXE_PATH, os.X_OK):
        raise OdaConversionError(
            f"ODA AppRun is not executable: '{ODA_EXE_PATH}'. "
            "Make sure the AppRun file has execute permission."
        )


# ---------------------------------------------------------------------------
# BUILD ODA LIBRARY PATH
# ---------------------------------------------------------------------------

def _build_oda_environment() -> dict:
    """
    Build the environment used to launch ODA.

    Render does not provide AppImage/FUSE support, so we run the extracted
    AppImage contents directly.

    ODA contains libraries such as libTD_Db.so inside the extracted tree.
    We locate those library directories and add them to LD_LIBRARY_PATH.
    """

    env = os.environ.copy()

    library_dirs = []

    # Common locations first.
    common_dirs = [
        ODA_ROOT,
        os.path.join(ODA_ROOT, "usr"),
        os.path.join(ODA_ROOT, "usr", "lib"),
        os.path.join(ODA_ROOT, "usr", "lib64"),
        os.path.join(ODA_ROOT, "lib"),
        os.path.join(ODA_ROOT, "lib64"),
    ]

    for directory in common_dirs:
        if os.path.isdir(directory):
            library_dirs.append(directory)

    # Find directories containing ODA shared libraries.
    # In particular, locate libTD_Db.so.
    try:
        for root, dirs, files in os.walk(ODA_ROOT):
            # Avoid unnecessary hidden directories.
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
            ]

            if "libTD_Db.so" in files:
                library_dirs.insert(0, root)

            # Add directories containing shared libraries.
            if any(
                filename.endswith(".so") or ".so." in filename
                for filename in files
            ):
                library_dirs.append(root)

    except OSError:
        pass

    # Remove duplicates while preserving order.
    unique_dirs = []
    seen = set()

    for directory in library_dirs:
        absolute_directory = os.path.abspath(directory)

        if absolute_directory not in seen:
            seen.add(absolute_directory)
            unique_dirs.append(absolute_directory)

    existing_ld_library_path = env.get("LD_LIBRARY_PATH", "")

    if existing_ld_library_path:
        unique_dirs.append(existing_ld_library_path)

    env["LD_LIBRARY_PATH"] = ":".join(unique_dirs)

    # AppRun / Qt applications sometimes need to know their own root.
    env["APPDIR"] = ODA_ROOT

    return env


# ---------------------------------------------------------------------------
# RUN ODA
# ---------------------------------------------------------------------------

def _run_oda(
    input_folder: str,
    output_folder: str,
    out_type: str,
    timeout: int,
) -> None:

    _validate_oda()

    if not os.path.isdir(input_folder):
        raise OdaConversionError(
            f"ODA input folder does not exist: '{input_folder}'"
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

    env = _build_oda_environment()

    creationflags = 0
    startupinfo = None

    # Windows handling.
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
            cwd=ODA_ROOT,
            env=env,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )

    except subprocess.TimeoutExpired as exc:

        raise OdaConversionError(
            f"ODA File Converter timed out after {timeout}s"
        ) from exc

    except OSError as exc:

        raise OdaConversionError(
            f"Failed to start ODA File Converter: {exc}"
        ) from exc

    if result.returncode != 0:

        raise OdaConversionError(
            f"ODA File Converter exited with code {result.returncode}.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    ext = out_type.lower()

    produced = [
        f
        for f in os.listdir(output_folder)
        if f.lower().endswith(f".{ext}")
    ]

    if not produced:

        raise OdaConversionError(
            f"ODA File Converter ran but produced no .{ext} files.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# PUBLIC CONVERSION FUNCTIONS
# ---------------------------------------------------------------------------

def convert_folder_to_dwg(
    input_folder: str,
    output_folder: str,
    timeout: int = 120,
) -> None:
    """
    Convert every DXF in input_folder to DWG.
    Results are written into output_folder.
    """

    _run_oda(
        input_folder,
        output_folder,
        "DWG",
        timeout,
    )


def convert_folder_to_dxf(
    input_folder: str,
    output_folder: str,
    timeout: int = 120,
) -> None:
    """
    Convert every DWG in input_folder to DXF.
    Results are written into output_folder.
    """

    _run_oda(
        input_folder,
        output_folder,
        "DXF",
        timeout,
    )


def convert_single_file_to_dxf(
    input_path: str,
    timeout: int = 60,
) -> str:
    """
    Convert one DWG file to DXF.

    A temporary input/output folder is used so concurrent jobs do not
    interfere with each other.
    """

    if not os.path.isfile(input_path):
        raise OdaConversionError(
            f"Input DWG file does not exist: '{input_path}'"
        )

    with tempfile.TemporaryDirectory() as tmp_in, \
         tempfile.TemporaryDirectory() as tmp_out:

        in_name = os.path.basename(input_path)

        tmp_in_path = os.path.join(
            tmp_in,
            in_name,
        )

        shutil.copy2(
            input_path,
            tmp_in_path,
        )

        convert_folder_to_dxf(
            tmp_in,
            tmp_out,
            timeout=timeout,
        )

        produced = [
            f
            for f in os.listdir(tmp_out)
            if f.lower().endswith(".dxf")
        ]

        if not produced:
            raise OdaConversionError(
                f"No DXF produced for '{input_path}'"
            )

        result_path = (
            input_path.rsplit(".", 1)[0]
            + "_converted.dxf"
        )

        shutil.move(
            os.path.join(tmp_out, produced[0]),
            result_path,
        )

        return result_path
