"""
MSDAC Relay Position ("R(POS)"/"R_POS") population + validation.

OPTIONAL step, only runs when the "Relay Position" checkbox is ticked in
the UI (CONFIRMED). When enabled, it runs against every circuit's
IN-MEMORY generated doc (before anything is saved to disk), so a vital
error can stop the whole job the same way contact-repetition errors do.

CONFIRMED requirements:
  - Position code = the relay's own Relay Rack position, e.g. rack 'R1',
    band 'A' (1st tag/type row-pair), column B (1st data column) ->
    "R1A1" (see relay_rack_core.build_relay_position_lookup()).

  - TWO matching modes, because not every contact/coil block carries the
    same attributes (CONFIRMED, measured directly from dxf_templates/):

      1. PRIMARY - blocks with BOTH S_NAME and R_NAME (every
         Front_Contact/Back_Contact everywhere, plus Signal's own
         Relay_Coil in STANDARD1-4/LCPR/ZRP): matched by the
         (S_NAME, R_NAME) PAIR against Relay Rack's (NAME1, NAME2).
         These blocks are tagged "R(POS)".

      2. FALLBACK - blocks with ONLY R_NAME, no S_NAME at all
         (CONFIRMED: AR & AZR's and Track's own relay coil, in
         RELAY_COIL.dxf/TSPR.dxf/TSPR1.dxf): matched by R_NAME ALONE
         against Relay Rack's NAME2 - CONFIRMED accepted as less
         precise/possibly ambiguous. These blocks are tagged "R_POS"
         (underscore, not parens) - CONFIRMED they get filled the same
         way as R(POS), just under their own tag name.

  - CONFIRMED (including the Track/SDF exception): this check applies
    to EVERY circuit type's contacts/coils, including Track/SDF's
    track-named contacts (S_NAME=track name) - those must also resolve
    to a Relay Rack entry, no exceptions.

  - VITAL (critical) errors - logged via log.error(), which the caller
    already treats as a stop-before-output condition (same as contact
    repetition):
      - PRIMARY mode: no Relay Rack entry has that (S_NAME, R_NAME) pair.
      - FALLBACK mode: no Relay Rack entry has that R_NAME at all.
    An AMBIGUOUS fallback match (more than one Relay Rack entry shares
    that R_NAME) is NOT treated as vital - it's logged as a WARNING
    instead, since the ambiguity itself was an accepted tradeoff, not a
    missing-data problem.
"""

import re

CONTACT_BLOCK_NAMES = ("Front_Contact", "Back_Contact", "SDF_CONTACT", "LCPR_FRONT", "LCPR_BACK CONTACT")
# CONFIRMED: TIMER_RELAY/SDF_RELAY are SDF-specific coil-equivalent
# blocks for timer relays - same S_NAME/R_NAME shape as Relay_Coil,
# just a different block name.
COIL_BLOCK_NAMES = ("Relay_Coil", "TIMER_RELAY", "SDF_RELAY")

PRIMARY_TAG = "R(POS)"
FALLBACK_TAG = "R_POS"


def _inserts_named(entities, *prefixes):
    return [
        e for e in entities
        if e.dxftype() == "INSERT" and any(e.dxf.name.startswith(p) for p in prefixes)
    ]


def apply_hut_name_keyword(pending: list, hut_name_input) -> int:
    """
    CONFIRMED: generic, circuit-type-agnostic "HUT NAME" keyword
    substitution - scans every TEXT/MTEXT entity and every block
    attribute value across every sheet in `pending`, replacing the
    literal string "HUT NAME" wherever it appears with the user-entered
    global HUT Name input field value. Applies uniformly to AR & AZR,
    SDF, Relay Rack, Communication, Data Logger, and Custom Circuits -
    any circuit type whose templates might contain this keyword,
    without needing a dedicated per-module substitution function like
    Signal and Track already have. No-op if hut_name_input is empty.
    Returns the number of replacements made.
    """
    if not hut_name_input:
        return 0
    replaced = 0
    for _final_name, doc in pending:
        for e in doc.modelspace():
            if e.dxftype() == "TEXT" and e.dxf.text and "HUT NAME" in e.dxf.text:
                e.dxf.text = e.dxf.text.replace("HUT NAME", str(hut_name_input))
                replaced += 1
            elif e.dxftype() == "MTEXT" and e.text and "HUT NAME" in e.text:
                e.text = e.text.replace("HUT NAME", str(hut_name_input))
                replaced += 1
            elif e.dxftype() == "INSERT":
                for att in e.attribs:
                    if att.dxf.text and "HUT NAME" in att.dxf.text:
                        att.dxf.text = att.dxf.text.replace("HUT NAME", str(hut_name_input))
                        replaced += 1
    return replaced


def apply_filename_attribute(pending: list) -> int:
    """
    CONFIRMED: populates a "FILENAME" attribute on every generated
    sheet's own TITLE block with that sheet's actual output filename
    (without the .dxf extension) - a reliable, static replacement for
    AutoCAD Field objects, which don't survive being copied via ezdxf
    (triggers a "copy process ignored FIELD" warning and silently
    drops the live reference). Only sets the value if the block
    instance ALREADY has a "FILENAME" attribute (i.e. the template's
    own TITLE block has been updated with a matching ATTDEF) - doesn't
    forcibly add a new attribute to templates that don't have one yet,
    since it wouldn't display correctly without a matching ATTDEF
    defining its position/style. Returns the number of sheets updated.
    """
    updated = 0
    for final_name, doc in pending:
        filename_no_ext = final_name.rsplit(".", 1)[0] if "." in final_name else final_name
        for e in doc.modelspace():
            if e.dxftype() != "INSERT" or e.dxf.name not in ("TITLE", "TITLEBLOCK"):
                continue
            for att in e.attribs:
                if att.dxf.tag == "FILENAME":
                    att.dxf.text = filename_no_ext
                    updated += 1
    return updated


def clear_placeholder_values(pending: list) -> int:
    """
    CONFIRMED: any contact/coil attribute still showing its raw template
    placeholder text (a value composed ENTIRELY of 'X'/'x' characters,
    e.g. S_NAME='XXXX', R(POS)='XXXXX') should be cleared to blank
    rather than left showing the literal placeholder - most relevant
    when Relay Position wasn't run (so R(POS) never got a real value),
    but also catches cases like SDF_RELAY's S_NAME, which is left
    untouched by circuit generation itself (S_NAME 'left untouched by
    design' - see contact_analysis_core.py) and so keeps its raw
    template placeholder unless cleared here. Runs unconditionally
    (harmless no-op on any attribute that already has a real value,
    since a real value is never purely X characters).
    Returns the number of attributes cleared, for logging.
    """
    cleared = 0
    for _final_name, doc in pending:
        for e in doc.modelspace():
            if e.dxftype() != "INSERT":
                continue
            if not (e.dxf.name.startswith(CONTACT_BLOCK_NAMES) or e.dxf.name.startswith(COIL_BLOCK_NAMES)):
                continue
            for att in e.attribs:
                text = (att.dxf.text or "").strip()
                if text and all(c.upper() == "X" for c in text):
                    att.dxf.text = ""
                    cleared += 1
    return cleared


def apply_relay_positions(pending: list, position_lookup: tuple, log, filename_to_circuit_type: dict = None) -> None:
    """
    pending: list of (final_name, doc) tuples - the SAME in-memory ezdxf
        documents app.py is about to save. Mutated in place.
    position_lookup: (by_pair, by_type) tuple from
        relay_rack_core.build_relay_position_lookup().
    log: GenerationLog.
    filename_to_circuit_type: optional {final_name: "SIGNAL"/"TRACK"/etc.}
        map, so errors are labeled by circuit type - CONFIRMED requirement.
        Falls back to the filename itself if not given/not found (e.g.
        individual per-circuit endpoints don't need this, since every
        error there is already implicitly one circuit type).
    """
    by_pair, by_type = position_lookup
    filename_to_circuit_type = filename_to_circuit_type or {}

    for final_name, doc in pending:
        circuit_type = filename_to_circuit_type.get(final_name, final_name)
        msp = doc.modelspace()

        # CONFIRMED: Communication contacts/coils only count if their
        # row-group's home location (GROUP_LOC, embedded on the title
        # block) matches this panel's LOC1 (for contacts) or LOC2 (for
        # coils) - same rule contact_analysis_core.py already applies.
        # Without this, Relay Position would try (and fail) to find a
        # rack position for contacts/coils that Contact Analysis would
        # have correctly excluded as "not this panel's".
        communication_loc1 = None
        communication_loc2 = None
        group_location = ""
        if circuit_type == "COMMUNICATION":
            for title_ins in _inserts_named(msp, "TITLE", "TITLEBLOCK"):
                title_att = {a.dxf.tag: (a.dxf.text or "").strip() for a in title_ins.attribs}
                group_location = title_att.get("GROUP_LOC", "").strip().upper()
                # CONFIRMED FIX: read LOC1/LOC2 directly from their own
                # hidden attributes rather than regex-parsing the TITLE
                # text - a hut name containing its own hyphen (e.g.
                # "MSDAC HUT-1") broke the old regex-based split.
                if "LOC1" in title_att:
                    communication_loc1 = title_att.get("LOC1", "").strip().upper()
                    communication_loc2 = title_att.get("LOC2", "").strip().upper()
                break

        for block_names, is_coil in ((CONTACT_BLOCK_NAMES, False), (COIL_BLOCK_NAMES, True)):
            for e in _inserts_named(msp, *block_names):
                att_map = {a.dxf.tag: (a.dxf.text or "").strip() for a in e.attribs}
                has_s_name_tag = "S_NAME" in att_map
                r_name = att_map.get("R_NAME", "")
                s_name = att_map.get("S_NAME", "")

                if not r_name and not s_name:
                    continue  # not a real contact/coil placement (e.g. spare/blank)

                # CONFIRMED: "SPARE" is Data Logger's own padding marker
                # (fills out empty slots on its last sheet purely for visual
                # layout) - not a real relay reference, so it's skipped here
                # the same way contact_analysis_core.py already does.
                if r_name.strip().upper() == "SPARE":
                    continue

                if communication_loc1 is not None:
                    expected_loc = communication_loc2 if is_coil else communication_loc1
                    if group_location != expected_loc:
                        continue  # not this panel's location - skip, not an error

                if has_s_name_tag:
                    # PRIMARY mode: match by the (S_NAME, R_NAME) pair.
                    # CONFIRMED: the Relay Rack always has a direct
                    # entry for every relay/contact type used, including
                    # derived contact types (DECR, HECPR, HRP1, etc.) -
                    # a straight lookup is all that's needed.
                    position = by_pair.get((s_name.upper(), r_name.upper()))
                    if position is None:
                        log.error(f"[{circuit_type}] Relay position not found: {s_name} {r_name}")
                        continue
                    _set_or_add_attrib(e, PRIMARY_TAG, position)
                else:
                    # FALLBACK mode: no S_NAME on this block at all (AR & AZR /
                    # Track's own coil) - match by R_NAME alone, CONFIRMED
                    # accepted as less precise.
                    if not r_name:
                        continue
                    position = by_type.get(r_name.upper(), "__NOT_FOUND__")
                    if position == "__NOT_FOUND__":
                        log.error(f"[{circuit_type}] Relay position not found: {r_name}")
                        continue
                    if position is None:
                        log.warning(
                            f"[{circuit_type}] Relay position ambiguous for {r_name} "
                            "(matches more than one Relay Rack entry) - left blank."
                        )
                        continue
                    _set_or_add_attrib(e, FALLBACK_TAG, position)


def _set_or_add_attrib(insert_entity, tag: str, value: str) -> None:
    for att in insert_entity.attribs:
        if att.dxf.tag == tag:
            att.dxf.text = value
            return
    # Block instance doesn't already carry this attribute - add it
    # directly as a plain ATTRIB so it still shows up when opened in CAD.
    insert_entity.add_attrib(tag, value)
