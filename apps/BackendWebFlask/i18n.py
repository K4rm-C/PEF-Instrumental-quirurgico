"""
Locale helpers shared by app.py (Flask-Babel locale selector), controllers and services:
locale resolution, extraction marker N_() and presentation labels for DB values.

Public locale codes are BCP 47 (`en`, `es-MX`): they are what user.ui_preferences.locale,
the `pef_locale` cookie and <html lang> carry. Babel catalogs use the POSIX form
(`es_MX`, translations/es_MX/LC_MESSAGES), so babel_locale() converts only at that edge.
English is the source language: msgids are the English UI text, so `en` has no catalog.
"""

import flask_babel
from flask import g, request


DEFAULT_LOCALE = 'en'
SUPPORTED_LOCALES = ('en', 'es-MX')
LOCALE_COOKIE = 'pef_locale'
LOCALE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def N_(text):
    """Mark a code-controlled label for pybabel extraction without translating it here.

    The value stays English in Python (it may be compared or used as a lookup key) and is
    translated where it is displayed, with `_()` in the template.
    """
    return text


def normalize_locale(value):
    """Public locale code for any input: `es` / `es_MX` map to `es-MX`; unknown -> `en`."""
    locale = str(value or '').strip().replace('_', '-')
    if locale.lower() in {'es', 'es-mx'}:
        return 'es-MX'
    if locale.lower() == 'en':
        return 'en'
    return DEFAULT_LOCALE


def is_supported_locale(value):
    return value in SUPPORTED_LOCALES


def babel_locale(public_locale):
    """Babel/catalog identifier for a public locale code (es-MX -> es_MX)."""
    return normalize_locale(public_locale).replace('-', '_')


def resolve_locale(user):
    """Authenticated ui_preferences.locale wins; otherwise the pef_locale cookie; else `en`."""
    prefs = (user or {}).get('ui_preferences') or {}
    from_prefs = prefs.get('locale') or prefs.get('language')
    if from_prefs:
        return normalize_locale(from_prefs)
    return normalize_locale(request.cookies.get(LOCALE_COOKIE))


def select_locale():
    """Flask-Babel locale selector. Uses the user already verified for this request (if any)."""
    return babel_locale(resolve_locale(getattr(g, 'verified_user', None)))


def template_gettext(string, **variables):
    """Jinja `_()`: Flask-Babel gettext returning a plain (autoescaped) str.

    Templates also pass dynamic values through `_()` (labels marked with N_, but also
    DB/user text that is simply not in the catalog), so an empty value must stay empty:
    gettext('') would return the catalog header.
    """
    if not string:
        return string
    return flask_babel.gettext(string, **variables)


def template_ngettext(singular, plural, num, **variables):
    return flask_babel.ngettext(singular, plural, num, **variables)


def install_jinja_gettext(app):
    """Replace Flask-Babel's newstyle Jinja callables with plain-str ones.

    Newstyle gettext wraps every result in Markup and always applies `% variables`; with
    `_()` also applied to DB/user values that would disable autoescaping for them and break
    any value containing '%'. Old-style callables keep the escaping semantics the templates
    were written against while translations still come from Flask-Babel.
    """
    app.jinja_env.install_gettext_callables(
        gettext=template_gettext,
        ngettext=template_ngettext,
        newstyle=False,
    )


# ----------------------------------------------------------------------------------------
# Presentation localization of system-controlled values stored in PostgreSQL.
#
# The DB is never changed: names stay as stored (they may be Spanish, English or edited by
# an administrator). For display, a stable key (catalog `code`, record code or UUID) selects
# an English msgid that Flask-Babel translates for the current request. Anything without a
# mapping -- or whose stored text no longer matches a known system text -- is returned
# exactly as stored. Free text (people, notes, descriptions typed by users) is never mapped.
# ----------------------------------------------------------------------------------------

# Catalogs (cat_*) whose `name` is display text only: localized by `code` alone.
DB_CODE_LABELS = {
    'gender': {
        'female': N_('Female'),
        'male': N_('Male'),
        'other': N_('Other'),
        'unknown': N_('Not specified'),
    },
    'specialty': {
        'anesthesiology': N_('Anesthesiology'),
        'general_surgery': N_('General surgery'),
        'gynecology': N_('Gynecology and obstetrics'),
        'neurosurgery': N_('Neurosurgery'),
        'orthopedics': N_('Traumatology and orthopedics'),
        'urology': N_('Urology'),
    },
    'procedure': {
        'appendectomy': N_('Appendectomy'),
        'cesarean': N_('Cesarean section'),
        'hernia_repair': N_('Inguinal hernia repair'),
        'hip_replace': N_('Hip arthroplasty'),
        'lap_chole': N_('Laparoscopic cholecystectomy'),
        'open_chole': N_('Open cholecystectomy'),
    },
    'surgical_role': {
        'anesthesiologist': N_('Anesthesiologist'),
        'first_assistant': N_('First assistant'),
        'resident': N_('Resident physician'),
        'surgeon': N_('Surgeon'),
    },
    'operation_status': {
        'cancelled': N_('Cancelled'),
        'closed': N_('Closed'),
        'in_progress': N_('In Progress'),
        'scheduled': N_('Scheduled'),
    },
    'session_status': {
        'aborted': N_('Aborted'),
        'awaiting_spd_review': N_('Awaiting Review'),
        'closed': N_('Closed'),
        'correction_required': N_('Correction Required'),
        'in_progress': N_('In Progress'),
        'scheduled': N_('Scheduled'),
    },
    'cycle_status': {
        'available': N_('Available'),
        'in_use': N_('In Use'),
        'lost': N_('Lost / not recovered'),
        'maintenance': N_('Maintenance'),
        'reserved': N_('Reserved'),
        'retired': N_('Retired'),
        'sterilization': N_('Sterilization'),
    },
    'instrument_category': {
        'cutting': N_('Cutting'),
        'dissection': N_('Dissection'),
        'grasping': N_('Grasping'),
        'hemostasis': N_('Hemostasis'),
        'retraction': N_('Retraction'),
        'suction': N_('Suction'),
        'suturing': N_('Suturing'),
    },
    'usage_context': {
        'operative': N_('Operative use during surgery'),
        'pedagogical': N_('Teaching or training use'),
    },
    'discrepancy_reason': {
        'misclassified': N_('Piece classified in the wrong family'),
        'occluded': N_('Piece occluded or outside the region of interest'),
        'operator_error': N_('Operator capture or handling error'),
        'shortage': N_('Shortage against expected inventory'),
        'surplus': N_('Surplus against expected inventory'),
        'unidentified': N_('Piece present but not identified by the model'),
    },
    'checkpoint_reason': {
        'close': N_('Session close'),
        'discrepancy': N_('Discrepancy recorded'),
        'hourly': N_('Periodic capture'),
        'manual_pin': N_('Operator manual mark'),
        'start': N_('Session start'),
        'state_change': N_('Relevant state change'),
    },
    'operation_phase': {
        'closure': N_('Closure'),
        'demo_phase_1': N_('Demo phase one'),
        'demo_phase_2': N_('Demo phase two'),
        'demo_phase_3': N_('Demo phase three'),
        'final_count': N_('Final count'),
        'handover': N_('Tray handover and removal'),
        'intraop': N_('Intraoperative'),
        'pre_closure': N_('Count before cavity closure'),
        'pre_incision': N_('Initial count before incision'),
        'setup': N_('Table setup'),
        'start': N_('Start / initial stage'),
    },
    'event_type': {
        'auto_count': N_('Count suggested by the model'),
        'close_blocked': N_('Close attempt blocked'),
        'correction_applied': N_('Human correction applied'),
        'discrepancy_raised': N_('Discrepancy detected'),
        'manual_close': N_('Close with manual quantity report'),
        'manual_count': N_('Manually captured count'),
        'phase_change': N_('Surgical phase change'),
        'session_close': N_('Session close'),
        'session_open': N_('Session opened'),
        'validation_passed': N_('Validation without discrepancies'),
    },
    'processing_purpose': {
        'external_sharing': N_('Transfer to third parties'),
        'model_improvement': N_('Vision model improvement'),
        'quality_ops': N_('Quality and operation of the counting process'),
    },
}

# Records that administrators can edit: key (code or UUID) -> {known stored text: msgid}.
# A stored text is localized only while it is still one of the known system texts for that
# key (the Docker demo DB and the repository seeds use different texts for the same keys).
DB_RECORD_TEXTS = {
    'role': {
        'station_operator': {
            'Operador de estacion. Ejecuta sesiones de conteo asignadas.':
                N_('Station operator. Runs assigned counting sessions.'),
        },
        'spd_supervisor': {
            'Supervisor SPD. Programa sesiones, aviso de privacidad y cierra casos.':
                N_('SPD supervisor. Schedules sessions, confirms the privacy notice and closes cases.'),
        },
        'it_admin': {
            'Administrador Tecnico. Gestiona catalogos, usuarios y configuracion.':
                N_('Technical administrator. Manages catalogs, users and configuration.'),
        },
    },
    'operating_room': {
        'OR-01': {'Quirofano 1': N_('Operating Room 1')},
    },
    'capture_station': {
        '13131313-0000-4000-8000-000000000001': {
            'Estacion cenital OR-01': N_('Overhead Capture Station OR-01'),
            'Capture Station CS-01': N_('Capture Station CS-01'),
        },
    },
    'kit': {
        '51515151-0000-4000-8000-000000000001': {
            'Kit laparoscopico basico': N_('Basic laparoscopic kit'),
            'Video Demo Kit 1': N_('Video Demo Kit 1'),
        },
        '51515151-0000-4000-8000-000000000002': {
            'Video Demo Kit 2': N_('Video Demo Kit 2'),
        },
    },
    'instrument_family': {
        'KELLY': {'Pinza Kelly': N_('Kelly forceps')},
        'METZ': {'Tijera Metzenbaum': N_('Metzenbaum scissors')},
        'MAYOHEG': {
            'Portaagujas Mayo-Hegar': N_('Mayo-Hegar needle holder'),
            'Mayo-Hegar needle holder': N_('Mayo-Hegar needle holder'),
        },
        'FARABEUF': {
            'Separador Farabeuf': N_('Farabeuf retractor'),
            'Farabeuf retractor': N_('Farabeuf retractor'),
        },
        'POZZI': {'Pozzi forceps': N_('Pozzi forceps')},
        'SCALPEL3': {'Scalpel handle #3': N_('Scalpel handle #3')},
        'MAYO_CURVED': {'Curved Mayo Scissor': N_('Curved Mayo Scissor')},
        'MAYO_STRAIGHT': {'Straight Mayo Scissor': N_('Straight Mayo Scissor')},
        'KELLY_FORCEPS': {'Straight Kelly Forceps': N_('Straight Kelly Forceps')},
        'ALLIS': {'Allis forceps': N_('Allis forceps')},
        'FOERSTER': {'Foerster forceps': N_('Foerster forceps')},
        'ROCHESTER': {'Rochester-Pean forceps': N_('Rochester-Pean forceps')},
        'OLSENHEG': {'Olsen-Hegar needle holder': N_('Olsen-Hegar needle holder')},
        'ADSON_PLAIN': {'Adson without teeth': N_('Adson without teeth')},
        'ADSON_TEETH': {'Adson with teeth': N_('Adson with teeth')},
        'MOSQUITO': {'Curve Mosquito Kocher': N_('Curve Mosquito Kocher')},
        'SCALPEL4': {'Scalpel n4 S': N_('Scalpel n4 S')},
        'CRILE_WOOD': {'Crile Wood': N_('Crile Wood')},
        'PROBE': {'Surgical probe': N_('Surgical probe')},
    },
    'instrument_family.identify': {
        'KELLY': {'Pinza recta o curva con cremallera y ranuras transversales en la punta que no llegan al extremo.':
                  N_('Straight or curved forceps with a ratchet and transverse serrations at the tip that do not reach the end.')},
        'METZ': {'Tijera de hojas delgadas y cortas respecto a la longitud del mango.':
                 N_('Scissors with thin blades that are short relative to the handle length.')},
        'MAYOHEG': {
            'Similar a una pinza pero con puntas cortas, anchas y ranurado cruzado.':
                N_('Similar to forceps but with short, broad tips and cross-hatched grooves.'),
            'Needle holder with short, broad jaws and cross-hatched grip.':
                N_('Needle holder with short, broad jaws and cross-hatched grip.'),
        },
        'FARABEUF': {
            'Lamina metalica doblada en ambos extremos, sin cremallera ni articulacion.':
                N_('Metal blade bent at both ends, with no ratchet or joint.'),
            'Double-ended flat metal blade bent at both ends; no ratchet.':
                N_('Double-ended flat metal blade bent at both ends; no ratchet.'),
        },
        'POZZI': {'Forceps with interlocking jaws used for tissue or sponge handling.':
                  N_('Forceps with interlocking jaws used for tissue or sponge handling.')},
        'SCALPEL3': {'Flat handle with a distal slot for disposable blades (size #3).':
                     N_('Flat handle with a distal slot for disposable blades (size #3).')},
        'MAYO_CURVED': {'Heavy scissors with curved blades and blunt tips.':
                        N_('Heavy scissors with curved blades and blunt tips.')},
        'MAYO_STRAIGHT': {'Heavy scissors with straight blades and blunt tips.':
                          N_('Heavy scissors with straight blades and blunt tips.')},
        'KELLY_FORCEPS': {'Hemostatic forceps with partial-jaw serrations (Kelly profile).':
                          N_('Hemostatic forceps with partial-jaw serrations (Kelly profile).')},
        'ALLIS': {'Ring forceps with multiple interlocking teeth at the tip.':
                  N_('Ring forceps with multiple interlocking teeth at the tip.')},
        'FOERSTER': {'Long ring forceps with oval fenestrated jaws (sponge stick).':
                     N_('Long ring forceps with oval fenestrated jaws (sponge stick).')},
        'ROCHESTER': {'Hemostatic forceps with transverse serrations extending to the tip.':
                      N_('Hemostatic forceps with transverse serrations extending to the tip.')},
        'OLSENHEG': {'Needle holder with built-in suture-cutting scissors near the jaws.':
                     N_('Needle holder with built-in suture-cutting scissors near the jaws.')},
        'ADSON_PLAIN': {'Fine tissue forceps with serrated tips and no teeth.':
                        N_('Fine tissue forceps with serrated tips and no teeth.')},
        'ADSON_TEETH': {'Fine tissue forceps with 1x2 teeth at the tip.':
                        N_('Fine tissue forceps with 1x2 teeth at the tip.')},
        'MOSQUITO': {'Fine curved hemostatic forceps (mosquito / Kocher profile).':
                     N_('Fine curved hemostatic forceps (mosquito / Kocher profile).')},
        'SCALPEL4': {'Scalpel handle sized for #4 blades (heavier than #3).':
                     N_('Scalpel handle sized for #4 blades (heavier than #3).')},
        'CRILE_WOOD': {'Needle holder with Crile-Wood jaw pattern.':
                       N_('Needle holder with Crile-Wood jaw pattern.')},
        'PROBE': {'Slender probe used to explore tracts or cavities.':
                  N_('Slender probe used to explore tracts or cavities.')},
    },
    'instrument_family.classify': {
        'KELLY': {'Instrumental de hemostasia. Se distingue de la Rochester por el estriado parcial.':
                  N_('Hemostasis instrument. Distinguished from the Rochester by its partial serration.')},
        'METZ': {'Instrumental de corte para tejido delicado, no para sutura.':
                 N_('Cutting instrument for delicate tissue, not for suture.')},
        'MAYOHEG': {
            'Instrumental de sutura. Se diferencia de la pinza por el ranurado en rejilla.':
                N_('Suturing instrument. Differs from forceps by its grid-pattern grooves.'),
            'Suturing instrument without integrated scissors.':
                N_('Suturing instrument without integrated scissors.'),
        },
        'FARABEUF': {
            'Instrumental de separacion de uso manual, siempre en par.':
                N_('Manual retraction instrument, always used in pairs.'),
            'Retraction instrument; used in pairs.':
                N_('Retraction instrument; used in pairs.'),
        },
        'POZZI': {'Grasping instrument; longer jaws than Adson, typically ring-handled.':
                  N_('Grasping instrument; longer jaws than Adson, typically ring-handled.')},
        'SCALPEL3': {'Cutting instrument; no scissors blades, only a blade mount.':
                     N_('Cutting instrument; no scissors blades, only a blade mount.')},
        'MAYO_CURVED': {'Cutting instrument for denser tissue; curved blade profile.':
                        N_('Cutting instrument for denser tissue; curved blade profile.')},
        'MAYO_STRAIGHT': {'Cutting instrument for denser tissue; straight blade profile.':
                          N_('Cutting instrument for denser tissue; straight blade profile.')},
        'KELLY_FORCEPS': {'Hemostasis instrument; distinct from Mayo scissors and Rochester-Pean.':
                          N_('Hemostasis instrument; distinct from Mayo scissors and Rochester-Pean.')},
        'ALLIS': {'Grasping instrument for firmer tissue purchase.':
                  N_('Grasping instrument for firmer tissue purchase.')},
        'FOERSTER': {'Grasping instrument for sponges; fenestrated jaws.':
                     N_('Grasping instrument for sponges; fenestrated jaws.')},
        'ROCHESTER': {'Hemostasis instrument; fuller serration than classic Kelly forceps.':
                      N_('Hemostasis instrument; fuller serration than classic Kelly forceps.')},
        'OLSENHEG': {'Suturing instrument; combined holder and cutter.':
                     N_('Suturing instrument; combined holder and cutter.')},
        'ADSON_PLAIN': {'Grasping instrument for delicate tissue; plain tips.':
                        N_('Grasping instrument for delicate tissue; plain tips.')},
        'ADSON_TEETH': {'Grasping instrument for delicate tissue; toothed tips.':
                        N_('Grasping instrument for delicate tissue; toothed tips.')},
        'MOSQUITO': {'Hemostasis instrument; smaller jaws than Kelly or Rochester.':
                     N_('Hemostasis instrument; smaller jaws than Kelly or Rochester.')},
        'SCALPEL4': {'Cutting instrument; blade mount for larger blades.':
                     N_('Cutting instrument; blade mount for larger blades.')},
        'CRILE_WOOD': {'Suturing instrument; finer grip than Mayo-Hegar.':
                       N_('Suturing instrument; finer grip than Mayo-Hegar.')},
        'PROBE': {'Dissection / exploration instrument; no cutting edge.':
                  N_('Dissection / exploration instrument; no cutting edge.')},
    },
    'instrument_family.function': {
        'KELLY': {'Ocluir vasos sanguineos de calibre pequeno y mediano durante la diseccion.':
                  N_('Occlude small and medium vessels during dissection.')},
        'METZ': {'Cortar y disecar tejido fino sin danar estructuras adyacentes.':
                 N_('Cut and dissect fine tissue without damaging adjacent structures.')},
        'MAYOHEG': {
            'Sujetar la aguja con firmeza durante el paso de sutura.':
                N_('Hold the needle firmly while passing the suture.'),
            'Hold the needle firmly while passing suture.':
                N_('Hold the needle firmly while passing suture.'),
        },
        'FARABEUF': {
            'Retraer bordes de la herida para exponer el campo quirurgico.':
                N_('Retract the wound edges to expose the surgical field.'),
            'Retract wound edges to expose the field.':
                N_('Retract wound edges to expose the field.'),
        },
        'POZZI': {'Hold tissue or sponges during operative exposure.':
                  N_('Hold tissue or sponges during operative exposure.')},
        'SCALPEL3': {'Mount a blade to make controlled skin or tissue incisions.':
                     N_('Mount a blade to make controlled skin or tissue incisions.')},
        'MAYO_CURVED': {'Cut fascia or tougher tissue along a curved path.':
                        N_('Cut fascia or tougher tissue along a curved path.')},
        'MAYO_STRAIGHT': {'Cut suture or tougher tissue in a straight line.':
                          N_('Cut suture or tougher tissue in a straight line.')},
        'KELLY_FORCEPS': {'Clamp vessels or tissue for hemostasis.':
                          N_('Clamp vessels or tissue for hemostasis.')},
        'ALLIS': {'Hold or retract denser tissue edges.': N_('Hold or retract denser tissue edges.')},
        'FOERSTER': {'Hold sponges for blotting or packing.': N_('Hold sponges for blotting or packing.')},
        'ROCHESTER': {'Clamp larger vessels or pedicles.': N_('Clamp larger vessels or pedicles.')},
        'OLSENHEG': {'Hold the needle and cut suture without changing instruments.':
                     N_('Hold the needle and cut suture without changing instruments.')},
        'ADSON_PLAIN': {'Handle skin or delicate tissue with less trauma.':
                        N_('Handle skin or delicate tissue with less trauma.')},
        'ADSON_TEETH': {'Hold skin or fascia with a more secure bite.':
                        N_('Hold skin or fascia with a more secure bite.')},
        'MOSQUITO': {'Clamp small vessels in delicate fields.': N_('Clamp small vessels in delicate fields.')},
        'SCALPEL4': {'Mount a #4 blade for larger incisions.': N_('Mount a #4 blade for larger incisions.')},
        'CRILE_WOOD': {'Hold needles for finer suture work.': N_('Hold needles for finer suture work.')},
        'PROBE': {'Explore tracts or guide blunt dissection.': N_('Explore tracts or guide blunt dissection.')},
    },
}

# Known system texts without a stable key of their own (procedure_kit.technique_label).
DB_KNOWN_TEXTS = {
    'technique_label': {
        'Laparoscopica': N_('Laparoscopic'),
        'Video demo': N_('Video demo'),
        'Compact demo': N_('Compact demo'),
    },
}

# Stored text -> msgid for every known record text, so older demo DBs whose rows have other
# UUIDs still resolve (for example a kit named 'Kit laparoscopico basico' with a new id).
_DB_TEXT_ALIASES = {
    entity: {text: msgid for variants in by_key.values() for text, msgid in variants.items()}
    for entity, by_key in DB_RECORD_TEXTS.items()
}


def localize_db_label(entity, key, fallback=None):
    """Display label of a code-keyed catalog value; `fallback` (the stored name) if unmapped."""
    msgid = DB_CODE_LABELS.get(entity, {}).get(str(key)) if key is not None else None
    return flask_babel.gettext(msgid) if msgid else fallback


def localize_db_text(entity, key, value):
    """Display text of an editable record (key = code or UUID).

    Localized only when `value` is a known system text (for that key, or an alias of the
    same entity); otherwise the stored value is returned unchanged.
    """
    if not value:
        return value
    variants = DB_RECORD_TEXTS.get(entity, {}).get(str(key)) if key is not None else None
    msgid = (variants or {}).get(value) or _DB_TEXT_ALIASES.get(entity, {}).get(value)
    return flask_babel.gettext(msgid) if msgid else value


def localize_known_text(entity, value):
    """Display text for a known system value that has no key (e.g. a technique label)."""
    msgid = DB_KNOWN_TEXTS.get(entity, {}).get(value) if value else None
    return flask_babel.gettext(msgid) if msgid else value


def search_text(*values):
    """Lower-cased haystack mixing localized labels, stored DB labels and codes."""
    return ' '.join(str(value) for value in values if value).lower()


# ----------------------------------------------------------------------------------------
# Discrepancy descriptions. discrepancy.description is stored once, in whatever language the
# writer used (seed text in Spanish, services/rf_session.py text in English). Known automatic
# discrepancies are re-worded for display from their structured fields; the stored text is
# never parsed. Anything else keeps its stored description verbatim.
#
# Automatic discrepancies that exist in this system (all with reason shortage/surplus, a
# family, expected_quantity and detected_quantity):
#   origin event discrepancy_raised -> vision rule (expected_gt_detected): quantity detected
#   origin event manual_count       -> submit_manual_close (manual report or human
#                                      validation of a vision count): quantity reported
# ----------------------------------------------------------------------------------------

DISCREPANCY_SOURCE_BY_ORIGIN_EVENT = {
    'discrepancy_raised': 'detected',
    'manual_count': 'reported',
}


def discrepancy_source(origin_event_code):
    """'detected' | 'reported' | None (cannot be told apart safely)."""
    return DISCREPANCY_SOURCE_BY_ORIGIN_EVENT.get(origin_event_code)


def localize_discrepancy_description(*, reason_code, source, instrument, expected, actual,
                                     stored_description):
    """Display description of a discrepancy.

    reason_code: cat_discrepancy_reason.code; source: discrepancy_source(...); instrument:
    already localized family label; expected/actual: expected_quantity/detected_quantity.
    Returns stored_description unchanged unless this is a known automatic discrepancy.
    """
    if not instrument or expected is None or actual is None:
        return stored_description
    values = {'instrument': instrument, 'expected': expected}
    if reason_code == 'shortage' and actual < expected:
        if source == 'detected':
            return flask_babel.ngettext(
                '%(instrument)s: the kit expects %(expected)s, but only %(num)d was detected on the tray.',
                '%(instrument)s: the kit expects %(expected)s, but only %(num)d were detected on the tray.',
                actual, **values)
        if source == 'reported':
            return flask_babel.ngettext(
                '%(instrument)s: the kit expects %(expected)s, but only %(num)d was reported.',
                '%(instrument)s: the kit expects %(expected)s, but only %(num)d were reported.',
                actual, **values)
    elif reason_code == 'surplus' and actual > expected:
        if source == 'reported':
            return flask_babel.ngettext(
                '%(instrument)s: the kit expects %(expected)s, but %(num)d was reported.',
                '%(instrument)s: the kit expects %(expected)s, but %(num)d were reported.',
                actual, **values)
    else:
        return stored_description
    if source is not None:
        # Known origin, but a combination this system never generates: keep the stored text.
        return stored_description
    # Known reason, but the origin does not say whether it was detected or reported.
    return flask_babel.ngettext(
        '%(instrument)s: the kit expects %(expected)s, but %(num)d was recorded.',
        '%(instrument)s: the kit expects %(expected)s, but %(num)d were recorded.',
        actual, **values)
