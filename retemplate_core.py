"""
Re-template existing drawings: replaces an old title-block with a new
one across a set of already-generated drawing files, while preserving
whatever attribute values (SHT, CONT, TITLE, FILENAME, etc.) the old
block instance already had.

CONFIRMED workflow (from Krish directly):
  1. ERASE - remove the old title-block instance(s) matching a
     user-specified block name, from each target file.
  2. PURGE - after erasing, remove any block DEFINITION that now has
     zero instances left anywhere in the file (cleans the block table).
  3. INSERT the new template's own block, at the SAME location(s) the
     old one occupied, with the SAME attribute VALUES carried over
     (matched by tag name - e.g. whatever the old block's own SHT/
     CONT/TITLE/FILENAME held gets written onto the new block's own
     matching tags).
"""

import os
import ezdxf
import ezdxf.zoom as zoom
from ezdxf.addons import importer


def apply_new_template(target_paths: list, new_template_path: str, old_block_name: str, log, on_progress=None, original_names: dict = None, new_block_name_override: str = None) -> list:
    """
    target_paths: list of file paths to update in place.
    new_template_path: path to the uploaded new template .dxf - its
        content is just raw entities sitting directly in modelspace
        (border lines, title-block ATTDEFs, etc.), NOT already wrapped
        in a block definition - CONFIRMED, matching the same
        convention this app's own insert_border_title() already uses
        for the main generation flow. The block gets created at insert
        time, named after the template file itself (without its
        extension).
    old_block_name: the block name to erase from each target file
        (e.g. "TITLE_OLD").
    on_progress: optional callback(current_index, total, filename) -
        called before processing each file, so the caller can show
        real-time "currently processing: X" status.

    Returns the list of target_paths that were actually updated
    (skips - with a log warning - any file that doesn't contain the
    old block name at all, rather than treating it as an error, since
    a target zip may legitimately contain a mix of files).
    """
    border_doc = ezdxf.readfile(new_template_path)
    # CONFIRMED: the new block gets named after your ACTUAL uploaded
    # template file (e.g. "MSDAC_BORDER_2026"), not the internal path
    # this file happens to be saved under on disk.
    new_block_name = new_block_name_override or os.path.splitext(os.path.basename(new_template_path))[0]

    updated = []
    total = len(target_paths)
    for idx, path in enumerate(target_paths, start=1):
        if on_progress is not None:
            on_progress(idx, total, os.path.basename(path))

        try:
            doc = ezdxf.readfile(path)
        except Exception as e:
            log.warning(f"[RE-TEMPLATE] Could not read {os.path.basename(path)}: {e}")
            continue

        msp = doc.modelspace()
        old_instances = [
            e for e in msp
            if e.dxftype() == "INSERT" and e.dxf.name == old_block_name
        ]
        if not old_instances:
            # CONFIRMED: report what block names actually ARE present as
            # INSERTs in this file - lets the user see the real name to
            # compare against what they typed, instead of guessing why
            # a match failed (extra spaces, wrong file, etc.). Match
            # stays CASE-SENSITIVE (confirmed) - this is purely a
            # diagnostic aid, not a looser matching rule.
            actual_names = sorted({e.dxf.name for e in msp if e.dxftype() == "INSERT"})
            log.warning(
                f"[RE-TEMPLATE] {os.path.basename(path)}: no block named '{old_block_name}' found - "
                f"skipped. Blocks actually present: {', '.join(actual_names) if actual_names else '(none)'}"
            )
            continue

        # CONFIRMED: same conversion pattern as insert_border_title() -
        # create a NEW block definition in THIS doc, named after the
        # template file, and import the template's raw modelspace
        # content (lines, polylines, ATTDEFs, everything) directly
        # into it - only created ONCE per target file, then reused for
        # every old instance being replaced in that same file.
        if new_block_name not in doc.blocks:
            new_block = doc.blocks.new(name=new_block_name, base_point=(0, 0))
            imp = importer.Importer(border_doc, doc)
            imp.import_entities(border_doc.modelspace(), target_layout=new_block)
            imp.finalize()

        for old_e in old_instances:
            # CONFIRMED: preserve whatever attribute VALUES the old
            # instance had, keyed by tag name - carried over onto the
            # new block's matching tags (SHT, CONT, TITLE, FILENAME,
            # and anything else present on both).
            preserved_values = {att.dxf.tag: att.dxf.text for att in old_e.attribs}
            insert_point = old_e.dxf.insert

            new_e = msp.add_blockref(new_block_name, insert_point)
            new_e.add_auto_attribs({})  # populates default ATTDEF values first
            for att in new_e.attribs:
                if att.dxf.tag == "FILENAME":
                    # CONFIRMED: FILENAME always reflects the actual
                    # ORIGINAL file's own name (no extension) - NOT the
                    # old, preserved value, and NOT the internal
                    # "_converted" name used while processing a .dwg
                    # upload - since the whole point of this attribute
                    # is to always match the real, final file it's
                    # sitting inside, the same way it already works
                    # during normal generation.
                    current_basename = os.path.basename(path)
                    display_name = (original_names or {}).get(current_basename, current_basename)
                    att.dxf.text = os.path.splitext(display_name)[0]
                elif att.dxf.tag in preserved_values:
                    att.dxf.text = preserved_values[att.dxf.tag]

            # ERASE the old instance now that its replacement is placed.
            msp.delete_entity(old_e)

        # PURGE: remove any block DEFINITION with zero instances left
        # anywhere in the document (the old block's definition, plus
        # anything else that happened to be orphaned).
        doc.blocks.delete_all_blocks()

        # CONFIRMED: apply Zoom Extents (same as AutoCAD's "ZOOM E"
        # command) so the file opens showing the full drawing content
        # fit to view, rather than whatever viewport happened to be
        # active before the template swap.
        zoom.extents(msp)

        doc.saveas(path)
        updated.append(path)
        log.warning(f"[RE-TEMPLATE] {os.path.basename(path)}: replaced {len(old_instances)} instance(s) of '{old_block_name}' with '{new_block_name}'.")

    return updated
