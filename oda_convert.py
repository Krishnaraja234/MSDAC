"""
Wrapper around ODA File Converter for DXF <-> DWG conversion.

The ODA File Converter AppImage is extracted beforehand:

    ODAFileConverter/
        squashfs-root/
            AppRun
            libTD_Db.so
            ...
            
We execute the extracted AppRun directly instead of executing the
original .AppImage. This avoids the FUSE requirement on Render.

ODA File Converter is invoked PER FOLDER:
    ODAFileConverter <input_folder> <output_folder>
                      <output_version> <output_type>
                      <recurse> <audit>

Each generation job has its own input/output folders, so concurrent
jobs do not share conversion files.
"""

import os
import shutil
import subprocess
import sys
import tempfile


# ==============================================================
# ODA FILE CONVERTER CONFIGURATION
# ==============================================================

_APP_DIR = os.path.dirname(os.path.abspath(__file__))

ODA_ROOT_DIR = os.path.join(
    _APP_DIR,
    "ODAFileConverter",
    "squashfs-root",
)

ODA_EXE_PATH = os.path.join(
    ODA_ROOT_DIR,
    "AppRun",
)

# ODA output settings
ODA_OUTPUT_VERSION = "ACAD2013"
ODA_RECURSE = "0"
ODA_AUDIT = "1"


# ==============================================================
# ERROR TYPE
# ==============================================================

class OdaConversionError(RuntimeError):
    """Raised when ODA File Converter fails."""

    pass


# ==============================================================
# ENVIRONMENT SETUP
# ==============================================================

def _build_oda_environment():
    """
    Build the environment used to launch the extracted ODA AppImage.

    Render does not provide FUSE, so we run the extracted AppRun
    directly.

    LD_LIBRARY_PATH is configured so that libraries such as:

        libTD_Db.so

    can be found by the ODA executable.
    """

    env = os.environ.copy()

    if sys.platform == "win32":
        return env

    library_directories = [
        ODA_ROOT_DIR,

        os.path.join(
            ODA_ROOT_DIR,
            "usr",
            "lib",
        ),

        os.path.join(
            ODA_ROOT_DIR,
            "usr",
            "lib64",
        ),

        os.path.join(
            ODA_ROOT_DIR,
            "lib",
        ),

        os.path.join(
            ODA_ROOT_DIR,
            "lib64",
        ),

        os.path.join(
            ODA_ROOT_DIR,
            "usr",
            "lib",
            "x86_64-linux-gnu",
        ),
    ]

    # Keep only directories that actually exist.
    existing_directories = [
        path
        for path in library_directories
        if os.path.isdir(path)
    ]

    # Preserve any existing Render LD_LIBRARY_PATH.
    existing_ld_library_path = env.get(
        "LD_LIBRARY_PATH",
        "",
    )

    if existing_ld_library_path:
        existing_directories.append(
            existing_ld_library_path
        )

    if existing_directories:
        env["LD_LIBRARY_PATH"] = ":".join(
            existing_directories
        )

    # AppImage-compatible environment variable.
    env["APPDIR"] = ODA_ROOT_DIR

    # Render is headless.
    # Prevent Qt from trying to connect to a graphical display.
    env.setdefault(
        "QT_QPA_PLATFORM",
        "offscreen",
    )

    return env


# ==============================================================
# VALIDATE ODA INSTALLATION
# ==============================================================

def _validate_oda_installation():
    """
    Verify that the extracted ODA File Converter exists.
    """

    if not os.path.isdir(ODA_ROOT_DIR):
        raise OdaConversionError(
            "ODA File Converter extracted directory was not found.\n"
            f"Expected directory:\n{ODA_ROOT_DIR}\n\n"
            "Expected structure:\n"
            "ODAFileConverter/squashfs-root/AppRun"
        )

    if not os.path.isfile(ODA_EXE_PATH):
        raise OdaConversionError(
            "ODA File Converter AppRun was not found.\n"
            f"Expected file:\n{ODA_EXE_PATH}\n\n"
            "Expected structure:\n"
            "ODAFileConverter/squashfs-root/AppRun"
        )


# ==============================================================
# RUN ODA
# ==============================================================

def _run_oda(
    input_folder: str,
    output_folder: str,
    out_type: str,
    timeout: int,
) -> None:
    """
    Run ODA File Converter on a complete input folder.

    Parameters
    ----------
    input_folder:
        Folder containing DXF/DWG files.

    output_folder:
        Folder where converted files will be written.

    out_type:
        "DWG" or "DXF"

    timeout:
        Maximum execution time in seconds.
    """

    _validate_oda_installation()

    if not os.path.isdir(input_folder):
        raise OdaConversionError(
            f"ODA input folder does not exist:\n{input_folder}"
        )

    os.makedirs(
        output_folder,
        exist_ok=True,
    )

    # ----------------------------------------------------------
    # Linux executable permission
    # ----------------------------------------------------------
    #
    # Git on Windows can sometimes fail to preserve Linux
    # executable permissions.
    #
    # We therefore attempt to make AppRun executable when running
    # on Linux/Render.
    # ----------------------------------------------------------

    if sys.platform != "win32":
        try:
            current_mode = os.stat(
                ODA_EXE_PATH
            ).st_mode

            os.chmod(
                ODA_EXE_PATH,
                current_mode | 0o111,
            )

        except OSError as exc:
            raise OdaConversionError(
                "Unable to make ODA AppRun executable.\n"
                f"Path: {ODA_EXE_PATH}\n"
                f"Error: {exc}"
            ) from exc

    # ----------------------------------------------------------
    # ODA command line
    # ----------------------------------------------------------

    cmd = [
        ODA_EXE_PATH,
        input_folder,
        output_folder,
        ODA_OUTPUT_VERSION,
        out_type,
        ODA_RECURSE,
        ODA_AUDIT,
    ]

    # ----------------------------------------------------------
    # Windows process settings
    # ----------------------------------------------------------

    creationflags = 0
    startupinfo = None

    if sys.platform == "win32":

        creationflags = subprocess.CREATE_NO_WINDOW

        startupinfo = subprocess.STARTUPINFO()

        startupinfo.dwFlags |= (
            subprocess.STARTF_USESHOWWINDOW
        )

        startupinfo.wShowWindow = (
            subprocess.SW_HIDE
        )

    # ----------------------------------------------------------
    # Environment
    # ----------------------------------------------------------

    env = _build_oda_environment()

    # ----------------------------------------------------------
    # Run ODA
    # ----------------------------------------------------------

    try:

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
            startupinfo=startupinfo,
            env=env,
            cwd=ODA_ROOT_DIR,
        )

    except subprocess.TimeoutExpired as exc:

        raise OdaConversionError(
            "ODA File Converter timed out after "
            f"{timeout} seconds."
        ) from exc

    except PermissionError as exc:

        raise OdaConversionError(
            "Permission denied while starting ODA File Converter.\n"
            f"Executable:\n{ODA_EXE_PATH}\n"
            f"Error: {exc}"
        ) from exc

    except FileNotFoundError as exc:

        raise OdaConversionError(
            "ODA File Converter executable could not be started.\n"
            f"Executable:\n{ODA_EXE_PATH}\n"
            f"Error: {exc}"
        ) from exc

    except OSError as exc:

        raise OdaConversionError(
            "Operating system error while starting "
            "ODA File Converter.\n"
            f"Executable:\n{ODA_EXE_PATH}\n"
            f"Error: {exc}"
        ) from exc

    # ----------------------------------------------------------
    # Check return code
    # ----------------------------------------------------------

    if result.returncode != 0:

        raise OdaConversionError(
            "ODA File Converter exited with "
            f"code {result.returncode}.\n\n"
            f"Executable:\n{ODA_EXE_PATH}\n\n"
            f"Input folder:\n{input_folder}\n\n"
            f"Output folder:\n{output_folder}\n\n"
            f"stdout:\n{result.stdout}\n\n"
            f"stderr:\n{result.stderr}"
        )

    # ----------------------------------------------------------
    # Check output
    # ----------------------------------------------------------

    ext = out_type.lower()

    produced = [
        filename
        for filename in os.listdir(output_folder)
        if filename.lower().endswith(
            f".{ext}"
        )
    ]

    if not produced:

        raise OdaConversionError(
            "ODA File Converter completed successfully "
            "but produced no output files.\n\n"
            f"Expected extension: .{ext}\n"
            f"Output folder: {output_folder}\n\n"
            f"stdout:\n{result.stdout}\n\n"
            f"stderr:\n{result.stderr}"
        )


# ==============================================================
# DXF -> DWG
# ==============================================================

def convert_folder_to_dwg(
    input_folder: str,
    output_folder: str,
    timeout: int = 120,
) -> None:
    """
    Convert every DXF file in input_folder to DWG.

    Results are written into output_folder.
    """

    _run_oda(
        input_folder=input_folder,
        output_folder=output_folder,
        out_type="DWG",
        timeout=timeout,
    )


# ==============================================================
# DWG -> DXF
# ==============================================================

def convert_folder_to_dxf(
    input_folder: str,
    output_folder: str,
    timeout: int = 120,
) -> None:
    """
    Convert every DWG file in input_folder to DXF.

    Results are written into output_folder.
    """

    _run_oda(
        input_folder=input_folder,
        output_folder=output_folder,
        out_type="DXF",
        timeout=timeout,
    )


# ==============================================================
# SINGLE DWG -> DXF
# ==============================================================

def convert_single_file_to_dxf(
    input_path: str,
    timeout: int = 60,
) -> str:
    """
    Convert one DWG file to DXF.

    A temporary isolated input/output directory is used so that
    multiple users/jobs can perform conversions simultaneously.

    Returns
    -------
    str
        Path to the resulting converted DXF.
    """

    if not os.path.isfile(input_path):

        raise OdaConversionError(
            f"Input DWG file does not exist:\n{input_path}"
        )

    with tempfile.TemporaryDirectory() as tmp_in:

        with tempfile.TemporaryDirectory() as tmp_out:

            # --------------------------------------------------
            # Copy input DWG into isolated input folder
            # --------------------------------------------------

            input_filename = os.path.basename(
                input_path
            )

            temporary_input_path = os.path.join(
                tmp_in,
                input_filename,
            )

            shutil.copy2(
                input_path,
                temporary_input_path,
            )

            # --------------------------------------------------
            # Convert
            # --------------------------------------------------

            convert_folder_to_dxf(
                input_folder=tmp_in,
                output_folder=tmp_out,
                timeout=timeout,
            )

            # --------------------------------------------------
            # Find generated DXF
            # --------------------------------------------------

            produced = [
                filename
                for filename in os.listdir(tmp_out)
                if filename.lower().endswith(".dxf")
            ]

            if not produced:

                raise OdaConversionError(
                    "No DXF file was produced for:\n"
                    f"{input_path}"
                )

            # --------------------------------------------------
            # Move result next to original DWG
            # --------------------------------------------------

            result_path = (
                input_path.rsplit(".", 1)[0]
                + "_converted.dxf"
            )

            source_result = os.path.join(
                tmp_out,
                produced[0],
            )

            shutil.move(
                source_result,
                result_path,
            )

            return result_path
