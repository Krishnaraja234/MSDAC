```python
"""
Wrapper around ODA File Converter for DXF -> DWG conversion.

The ODA AppImage is extracted into:

    ODAFileConverter/
        squashfs-root/
            AppRun
            ...

We execute AppRun directly instead of executing the AppImage. This avoids
the FUSE requirement on Render/Linux.

Important:
- AppRun must be executed with squashfs-root as the working directory.
- ODA's internal library directories are added to LD_LIBRARY_PATH.
- Each conversion uses its own input/output folders.
"""

import os
import shutil
import subprocess
import sys


# ----------------------------------------------------------------------
# ODA CONFIGURATION
# ----------------------------------------------------------------------

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

# ----------------------------------------------------------------------

ODA_OUTPUT_VERSION = "ACAD2013"
ODA_RECURSE = "0"
ODA_AUDIT = "1"


class OdaConversionError(RuntimeError):
    pass


def _build_oda_environment() -> dict:
    """
    Build an environment suitable for running the extracted ODA AppImage.

    The important part is LD_LIBRARY_PATH. ODA contains private shared
    libraries inside squashfs-root which are not installed system-wide.
    """

    env = os.environ.copy()

    library_dirs = []

    # Common library locations inside an extracted AppImage.
    possible_dirs = [
        ODA_ROOT,
        os.path.join(ODA_ROOT, "lib"),
        os.path.join(ODA_ROOT, "lib64"),
        os.path.join(ODA_ROOT, "usr"),
        os.path.join(ODA_ROOT, "usr", "lib"),
        os.path.join(ODA_ROOT, "usr", "lib64"),
        os.path.join(ODA_ROOT, "usr", "lib", "x86_64-linux-gnu"),
    ]

    for directory in possible_dirs:
        if os.path.isdir(directory):
            library_dirs.append(directory)

    # Also search the extracted ODA tree for directories containing
    # important shared libraries such as libTD_Db.so.
    #
    # This makes the code robust if ODA stores its libraries in a
    # version-specific directory.
    for root, dirs, files in os.walk(ODA_ROOT):
        for filename in files:
            if filename.startswith("libTD_") and filename.endswith(".so"):
                if root not in library_dirs:
                    library_dirs.append(root)
                break

    # Preserve an existing LD_LIBRARY_PATH if Render/Linux already
    # provides one.
    existing_ld_library_path = env.get("LD_LIBRARY_PATH", "")

    if existing_ld_library_path:
        library_dirs.append(existing_ld_library_path)

    env["LD_LIBRARY_PATH"] = ":".join(library_dirs)

    # Some Qt-based AppImages expect their Qt plugins to be available
    # relative to the extracted application.
    qt_plugin_candidates = [
        os.path.join(ODA_ROOT, "plugins"),
        os.path.join(ODA_ROOT, "usr", "plugins"),
        os.path.join(ODA_ROOT, "usr", "lib", "qt", "plugins"),
    ]

    qt_plugin_dirs = [
        directory
        for directory in qt_plugin_candidates
        if os.path.isdir(directory)
    ]

    if qt_plugin_dirs:
        env["QT_PLUGIN_PATH"] = ":".join(qt_plugin_dirs)

    return env


def _run_oda(
    input_folder: str,
    output_folder: str,
    out_type: str,
    timeout: int,
) -> None:

    # --------------------------------------------------------------
    # Check extracted ODA installation
    # --------------------------------------------------------------

    if not os.path.isfile(ODA_EXE_PATH):
        raise OdaConversionError(
            f"ODA File Converter AppRun not found at:\n"
            f"{ODA_EXE_PATH}\n\n"
            "Expected structure:\n"
            "ODAFileConverter/\n"
            "    squashfs-root/\n"
            "        AppRun"
        )

    if not os.access(ODA_EXE_PATH, os.X_OK):
        raise OdaConversionError(
            f"ODA AppRun exists but is not executable:\n"
            f"{ODA_EXE_PATH}\n\n"
            "On Linux/Render, AppRun must have execute permission."
        )

    if not os.path.isdir(ODA_ROOT):
        raise OdaConversionError(
            f"ODA extracted directory not found:\n{ODA_ROOT}"
        )

    # --------------------------------------------------------------
    # Check for ODA's main private library
    # --------------------------------------------------------------

    lib_td_db_found = False

    for root, dirs, files in os.walk(ODA_ROOT):
        if "libTD_Db.so" in files:
            lib_td_db_found = True
            break

    if not lib_td_db_found:
        raise OdaConversionError(
            "ODA extraction appears incomplete.\n"
            "The required library 'libTD_Db.so' was not found inside:\n"
            f"{ODA_ROOT}"
        )

    # --------------------------------------------------------------
    # Prepare output folder
    # --------------------------------------------------------------

    os.makedirs(output_folder, exist_ok=True)

    # --------------------------------------------------------------
    # ODA command
    # --------------------------------------------------------------

    cmd = [
        ODA_EXE_PATH,
        input_folder,
        output_folder,
        ODA_OUTPUT_VERSION,
        out_type,
        ODA_RECURSE,
        ODA_AUDIT,
    ]

    # --------------------------------------------------------------
    # Environment
    # --------------------------------------------------------------

    env = _build_oda_environment()

    # --------------------------------------------------------------
    # Windows-specific process handling
    # --------------------------------------------------------------

    creationflags = 0
    startupinfo = None

    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    # --------------------------------------------------------------
    # Execute ODA
    # --------------------------------------------------------------

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,

            # IMPORTANT:
            # AppRun expects to run from the extracted AppImage root.
            cwd=ODA_ROOT,

            # IMPORTANT:
            # Gives ODA access to libTD_Db.so and its other private
            # shared libraries.
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
            f"Could not start ODA File Converter.\n"
            f"Executable: {ODA_EXE_PATH}\n"
            f"Error: {exc}"
        ) from exc

    # --------------------------------------------------------------
    # Check process result
    # --------------------------------------------------------------

    if result.returncode != 0:
        raise OdaConversionError(
            f"ODA File Converter exited with code "
            f"{result.returncode}.\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    # --------------------------------------------------------------
    # Check produced files
    # --------------------------------------------------------------

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


def convert_folder_to_dwg(
    input_folder: str,
    output_folder: str,
    timeout: int = 120,
) -> None:
    """Convert every DXF in input_folder to DWG."""
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
    """Convert every DWG in input_folder to DXF."""
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
    Convert a single DWG file to DXF.

    Uses isolated temporary input/output directories so multiple
    simultaneous jobs do not interfere with each other.
    """

    import tempfile

    with tempfile.TemporaryDirectory() as tmp_in, \
         tempfile.TemporaryDirectory() as tmp_out:

        in_name = os.path.basename(input_path)

        tmp_in_path = os.path.join(
            tmp_in,
            in_name,
        )

        shutil.copy(
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
                f"No DXF produced for {input_path}"
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
```
