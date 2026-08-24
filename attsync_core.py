"""
Generates a combined ARES Commander/AutoCAD SCR script that opens every
generated DWG file in turn, runs ATTSYNC on every distinct block used in
that file (syncing instances to match their current block definitions),
saves, and closes each file before moving to the next.

CONFIRMED syntax (from Krish's own working ARES Commander tool):
    OPEN "filename.dwg"
    ATTSYNC
    _NAME
    BLOCK1
    ATTSYNC
    _NAME
    BLOCK2
    -PURGE ALL 
    * N
    ZOOM E
    QSAVE
    CLOSE 

    (one ATTSYNC/_NAME/blockname sequence PER block - not
    comma-separated in a single call like AutoCAD's own documented
    syntax; ARES Commander needs each one issued separately. Also
    CONFIRMED: no "_." or "-" prefix on OPEN/ATTSYNC/QSAVE/CLOSE - those
    are AutoCAD's own alternate syntax, not what actually works here.)

CONFIRMED syntax for real AutoCAD (from Krish's own confirmed example):
each file is opened ONCE - the file's first block's "ATTSYNC NAME"
combines with that OPEN line, then the block name alone on its own
line. Every SUBSEQUENT block in that same file gets its own bare
"ATTSYNC NAME" line (no repeated OPEN) followed by its block name on
the next line. The purge/zoom/save/close sequence appears once, after
the file's last block:
    OPEN "filename.dwg" ATTSYNC NAME
    BLOCK1
    ATTSYNC NAME
    BLOCK2
    ATTSYNC NAME
    BLOCK3
    -PURGE ALL * N ZOOM E QSAVE CLOSE

Uses RELATIVE filenames (not absolute paths) for the OPEN command,
since the SCR is meant to be run from wherever the user extracts the
output zip - the SCR file must sit in the SAME folder as the DWG files.
"""

import ezdxf
import os


def get_block_names_used(dxf_path: str) -> list:
    """Returns the sorted list of distinct block names actually placed
    (via INSERT) in this DXF file's modelspace."""
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        return []
    msp = doc.modelspace()
    names = {e.dxf.name for e in msp if e.dxftype() == "INSERT"}
    return sorted(names)


def generate_attsync_scr(dxf_folder: str, dwg_extension: str = ".dwg", target: str = "ares") -> str:
    """
    dxf_folder: folder containing the generated .dxf files (used to read
        block names - NOT the dwg folder, since ezdxf can't read DWG).
    target: "ares" (CONFIRMED working, from Krish's own tested ARES
        Commander tool - one multi-line OPEN block covering every block
        name for that file) or "autocad" (CONFIRMED format from Krish's
        own ATTS.SCR/PWU.SCR examples - one complete self-contained line
        PER block name, no underscore prefix).
    Returns the full SCR file content as a string.
    """
    if target == "autocad":
        lines = []
        for fname in sorted(os.listdir(dxf_folder)):
            if not fname.lower().endswith(".dxf"):
                continue
            dxf_path = os.path.join(dxf_folder, fname)
            block_names = get_block_names_used(dxf_path)
            if not block_names:
                continue
            dwg_name = os.path.splitext(fname)[0] + dwg_extension
            lines.append(f'OPEN "{dwg_name}" ATTSYNC NAME')
            lines.append(block_names[0])
            for block_name in block_names[1:]:
                lines.append("ATTSYNC NAME")
                lines.append(block_name)
            lines.append("-PURGE ALL * N ZOOM E QSAVE CLOSE")
        return "\n".join(lines) + "\n"

    lines = []
    for fname in sorted(os.listdir(dxf_folder)):
        if not fname.lower().endswith(".dxf"):
            continue
        dxf_path = os.path.join(dxf_folder, fname)
        block_names = get_block_names_used(dxf_path)
        if not block_names:
            continue
        dwg_name = os.path.splitext(fname)[0] + dwg_extension
        lines.append(f'OPEN "{dwg_name}"')
        for block_name in block_names:
            lines.append("ATTSYNC")
            lines.append("_NAME")
            lines.append(block_name)
        lines.append("-PURGE ALL ")
        lines.append("* N")
        lines.append("ZOOM E")
        lines.append("QSAVE")
        lines.append("CLOSE ")
        lines.append("")
    return "\n".join(lines) + "\n"
