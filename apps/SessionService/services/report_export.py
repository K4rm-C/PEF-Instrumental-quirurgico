"""MS-06: export of the Supervisor report as CSV or PDF — GET /api/v1/sessions/reports/export.

The file is built from the SAME service methods as the screens (summary, stations, kits, operators) with the
SAME filters (from, to, tz, kit_id, operator_id, status, station_id), so a file can never disagree with the
view. Scope: the caller's institution (spd_supervisor). Every download is audited (access_audit
`report.export`, resource_type `report`, the filters in the correlation-safe columns only).

CSV: UTF-8 with BOM (spreadsheet friendly), sections separated by a blank line; any text cell starting with
= + - @ TAB or CR is prefixed with ' (no formula injection). Empty sections say so ("No data").
PDF: fpdf2 core fonts (Latin-1): typographic punctuation is normalised, other characters become "?".
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from errors import ApiError
from repositories import report_repository as repo
from services.session_reports import FILTERS

FORMATS = {"csv": "text/csv; charset=utf-8", "pdf": "application/pdf"}
LANGS = ("en", "es-MX")
MAX_PAGES = 50
NIL_UUID = uuid.UUID(int=0)

LABELS = {
    "en": {
        "title": "PEF - Supervisor report", "institution": "Institution", "from": "Period from", "to": "Period to",
        "tz": "Time zone", "filters": "Filters", "none": "none", "generated": "Generated at", "no_data": "No data",
        "section": "Section", "metric": "Metric", "value": "Value",
        "sessions": "Sessions", "outcomes": "Outcomes", "automatic": "Automatic count (YOLO)",
        "interventions": "Human interventions", "discrepancies": "Discrepancies", "agreement": "AI-human agreement",
        "activity": "Activity by day", "stations": "Activity by station", "kits": "Activity by kit",
        "operators": "Activity by operator", "by_reason": "Discrepancies by reason",
        "note": "AI-human agreement compares the AI suggestion with the human validation of the same count; "
                "it is not the accuracy of the model (no ground truth is recorded).",
        "metrics": {
            "total": "Total", "scheduled": "Scheduled", "in_progress": "In progress", "pending_review": "Pending review",
            "closed": "Closed", "reported": "Reported by the Operator", "avg_duration_minutes": "Average counting time (min)",
            "with_discrepancies_pct": "Reported sessions with discrepancies (%)", "approved": "Approved (safe close)", "approved_contingency": "Approved after a contingency count",
            "unresolved": "Closed as unresolved", "closed_without_record": "Closed without close record",
            "closed_by_operator": "Closed by the Operator", "aborted": "Aborted",
            "with_initial_count": "With initial count", "with_final_count": "With accepted final count",
            "final_first_attempt": "Final count accepted at the first attempt",
            "final_first_attempt_pct": "Final count accepted at the first attempt (%)",
            "sessions_with_rejected_final": "Sessions with a rejected final count",
            "rejected_final_attempts": "Rejected final count attempts", "recaptured_final": "Final count after a recapture",
            "contingency_reports": "Contingency reports", "avg_initial_to_final_minutes": "Average initial-to-final time (min)",
            "avg_start_to_initial_minutes": "Average start-to-initial time (min)", "human_corrections": "Human corrections",
            "discrepancies_resolved": "Discrepancies resolved", "open": "Open", "resolved": "Resolved",
            "avg_resolution_minutes": "Average resolution time (min)", "pairs": "Validated AI suggestions",
            "agreed": "Agreed", "agreement_pct": "Agreement (%)",
        },
        "columns": {
            "date": "Date", "sessions": "Sessions", "reported": "Reported", "with_discrepancies": "With discrepancies",
            "station": "Station", "room": "Room", "closed": "Closed", "with_final_count": "Final counts",
            "rejected_final_attempts": "Rejected final attempts", "cameras_used": "Cameras used",
            "avg_duration_minutes": "Avg duration (min)", "kit": "Kit", "version": "Version",
            "sessions_with_discrepancies": "With discrepancies", "discrepancies": "Discrepancies", "operator": "Operator",
            "counts_completed": "Counts completed", "human_corrections": "Human corrections",
            "agreement_pct": "Agreement (%)", "reason": "Reason", "total": "Total", "open": "Open",
        },
    },
    "es-MX": {
        "title": "PEF - Reporte del Supervisor", "institution": "Institución", "from": "Periodo desde",
        "to": "Periodo hasta", "tz": "Zona horaria", "filters": "Filtros", "none": "ninguno", "generated": "Generado",
        "no_data": "Sin datos", "section": "Sección", "metric": "Métrica", "value": "Valor",
        "sessions": "Sesiones", "outcomes": "Resultados", "automatic": "Conteo automático (YOLO)",
        "interventions": "Intervenciones humanas", "discrepancies": "Discrepancias",
        "agreement": "Concordancia IA-humano", "activity": "Actividad por día", "stations": "Actividad por estación",
        "kits": "Actividad por kit", "operators": "Actividad por operador", "by_reason": "Discrepancias por motivo",
        "note": "La concordancia IA-humano compara la sugerencia de la IA con la validación humana del mismo conteo; "
                "no es la precisión del modelo (no hay datos de referencia registrados).",
        "metrics": {
            "total": "Total", "scheduled": "Programadas", "in_progress": "En curso", "pending_review": "Pendientes de revisión",
            "closed": "Cerradas", "reported": "Reportadas por el Operador", "avg_duration_minutes": "Tiempo medio de conteo (min)",
            "with_discrepancies_pct": "Sesiones reportadas con discrepancias (%)", "approved": "Aprobadas (cierre seguro)", "approved_contingency": "Aprobadas tras un conteo de contingencia",
            "unresolved": "Cerradas como no resueltas", "closed_without_record": "Cerradas sin registro de cierre",
            "closed_by_operator": "Cerradas por el Operador", "aborted": "Abortadas",
            "with_initial_count": "Con conteo inicial", "with_final_count": "Con conteo final aceptado",
            "final_first_attempt": "Conteo final aceptado al primer intento",
            "final_first_attempt_pct": "Conteo final aceptado al primer intento (%)",
            "sessions_with_rejected_final": "Sesiones con conteo final rechazado",
            "rejected_final_attempts": "Intentos de conteo final rechazados",
            "recaptured_final": "Conteo final tras re-captura", "contingency_reports": "Informes de contingencia",
            "avg_initial_to_final_minutes": "Tiempo medio inicial-final (min)",
            "avg_start_to_initial_minutes": "Tiempo medio inicio-inicial (min)", "human_corrections": "Correcciones humanas",
            "discrepancies_resolved": "Discrepancias resueltas", "open": "Abiertas", "resolved": "Resueltas",
            "avg_resolution_minutes": "Tiempo medio de resolución (min)", "pairs": "Sugerencias de IA validadas",
            "agreed": "Coincidentes", "agreement_pct": "Concordancia (%)",
        },
        "columns": {
            "date": "Fecha", "sessions": "Sesiones", "reported": "Reportadas", "with_discrepancies": "Con discrepancias",
            "station": "Estación", "room": "Sala", "closed": "Cerradas", "with_final_count": "Conteos finales",
            "rejected_final_attempts": "Intentos finales rechazados", "cameras_used": "Cámaras usadas",
            "avg_duration_minutes": "Duración media (min)", "kit": "Kit", "version": "Versión",
            "sessions_with_discrepancies": "Con discrepancias", "discrepancies": "Discrepancias", "operator": "Operador",
            "counts_completed": "Conteos completados", "human_corrections": "Correcciones humanas",
            "agreement_pct": "Concordancia (%)", "reason": "Motivo", "total": "Total", "open": "Abiertas",
        },
    },
}

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formulas in text cells (numbers are kept as numbers)."""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def _all_pages(method, principal, filters, key) -> list[dict[str, Any]]:
    rows, page = [], 1
    while page <= MAX_PAGES:
        data = method(principal, {**filters, "page": str(page), "per_page": "100"})
        rows.extend(data[key])
        if page >= data["total_pages"]:
            break
        page += 1
    return rows


def collect(service, principal, args) -> dict[str, Any]:
    raw = {key: value for key, value in args.items()}
    fmt = (raw.pop("format", None) or "csv").strip().lower()
    lang = (raw.pop("lang", None) or "en").strip()
    if fmt not in FORMATS:
        raise ApiError.invalid_request("format must be csv or pdf", details={"field": "format", "allowed": list(FORMATS)})
    if lang not in LANGS:
        raise ApiError.invalid_request("lang must be en or es-MX", details={"field": "lang", "allowed": list(LANGS)})
    unknown = sorted(set(raw) - FILTERS)
    if unknown:
        raise ApiError.invalid_request("Unknown query parameter(s)", details={"unknown_parameters": unknown})
    summary = service.summary(principal, raw)
    data = {
        "format": fmt, "lang": lang, "summary": summary,
        "stations": _all_pages(service.stations, principal, raw, "stations"),
        "kits": _all_pages(service.kits, principal, raw, "kits"),
        "operators": _all_pages(service.operators, principal, raw, "operators"),
    }
    with service.database.read() as connection:
        data["institution"] = repo.institution_name(connection, principal.institution_id) or ""
    return data


def _audit(service, principal, correlation_id: str | None, fmt: str) -> None:
    with service.database.begin() as connection:
        connection.execute(text("""
            INSERT INTO access_audit (id, occurred_at, actor_type, actor_user_id, action, resource_type, resource_id,
                                      institution_id, outcome, correlation_id)
            VALUES (:id, now(), 'user', :actor, :action, 'report', :resource, :inst, 'success', :corr)
        """), {"id": uuid.uuid4(), "actor": principal.user_id, "action": f"report.export.{fmt}", "resource": NIL_UUID,
               "inst": principal.institution_id, "corr": (correlation_id or uuid.uuid4().hex)[:64]})


def _sections(data: dict[str, Any]) -> tuple[list[tuple[str, str, Any]], list[tuple[str, list[str], list[list[Any]]]]]:
    """(metric rows, tables) in the requested language."""
    L = data["lang"] and LABELS[data["lang"]]
    m, c, s = L["metrics"], L["columns"], data["summary"]
    sessions, outcomes, automatic = s["sessions"], s["outcomes"], s["automatic_counts"]
    interventions, discrepancies, agreement = s["interventions"], s["discrepancies"], s["ai_agreement"]
    metrics: list[tuple[str, str, Any]] = []
    for key in ("total", "scheduled", "in_progress", "pending_review", "closed", "reported", "avg_duration_minutes",
                "with_discrepancies_pct"):
        metrics.append((L["sessions"], m[key], sessions[key]))
    for key in ("approved", "approved_contingency", "unresolved", "closed_without_record", "closed_by_operator", "pending_review", "aborted"):
        metrics.append((L["outcomes"], m[key], outcomes[key]))
    for key in ("with_initial_count", "with_final_count", "final_first_attempt", "final_first_attempt_pct",
                "sessions_with_rejected_final", "rejected_final_attempts", "recaptured_final", "contingency_reports",
                "avg_start_to_initial_minutes", "avg_initial_to_final_minutes"):
        metrics.append((L["automatic"], m[key], automatic[key]))
    for key in ("human_corrections", "contingency_reports", "discrepancies_resolved", "rejected_final_attempts"):
        metrics.append((L["interventions"], m[key], interventions[key]))
    for key in ("total", "open", "resolved", "avg_resolution_minutes"):
        metrics.append((L["discrepancies"], m[key], discrepancies[key]))
    for key in ("pairs", "agreed", "agreement_pct"):
        metrics.append((L["agreement"], m[key], agreement[key]))
    tables = [
        (L["activity"], [c["date"], c["sessions"], c["reported"], c["with_discrepancies"]],
         [[d["date"], d["sessions"], d["reported"], d["with_discrepancies"]] for d in s["activity"]]),
        (L["stations"], [c["station"], c["room"], c["sessions"], c["reported"], c["closed"], c["with_discrepancies"],
                         c["with_final_count"], c["rejected_final_attempts"], c["cameras_used"], c["avg_duration_minutes"]],
         [[r["name"] or "-", r["room_code"] or "-", r["sessions"], r["reported"], r["closed"],
           r["sessions_with_discrepancies"], r["with_final_count"], r["rejected_final_attempts"], r["cameras_used"],
           r["avg_duration_minutes"]] for r in data["stations"]]),
        (L["by_reason"], [c["reason"], c["total"], c["open"]],
         [[r["name"] or r["code"], r["total"], r["open"]] for r in discrepancies["by_reason"]]),
        (L["kits"], [c["kit"], c["version"], c["sessions"], c["sessions_with_discrepancies"], c["discrepancies"]],
         [[k["name"] or "-", k["version"], k["sessions"], k["sessions_with_discrepancies"], k["discrepancies"]]
          for k in data["kits"]]),
        (L["operators"], [c["operator"], c["sessions"], c["counts_completed"], c["human_corrections"],
                          c["discrepancies"], c["agreement_pct"]],
         [[o["name"], o["sessions"], o["counts_completed"], o["human_corrections"], o["discrepancies"], o["agreement_pct"]]
          for o in data["operators"]]),
    ]
    return metrics, tables


def _header(data: dict[str, Any]) -> list[tuple[str, Any]]:
    L, f = LABELS[data["lang"]], data["summary"]["filters"]
    chosen = ", ".join(f"{key}={f[key]}" for key in ("kit_id", "operator_id", "station_id", "status") if f.get(key))
    return [(L["institution"], data["institution"]), (L["from"], f["from"]), (L["to"], f["to"]), (L["tz"], f["tz"]),
            (L["filters"], chosen or L["none"]),
            (L["generated"], datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))]


def to_csv(data: dict[str, Any]) -> bytes:
    L = LABELS[data["lang"]]
    metrics, tables = _sections(data)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([safe_cell(L["title"])])
    for label, value in _header(data):
        writer.writerow([safe_cell(label), safe_cell(value)])
    writer.writerow([])
    writer.writerow([L["section"], L["metric"], L["value"]])
    for row in metrics:
        writer.writerow([safe_cell(cell) for cell in row])
    for title, columns, rows in tables:
        writer.writerow([])
        writer.writerow([safe_cell(title)])
        writer.writerow([safe_cell(column) for column in columns])
        if not rows:
            writer.writerow([L["no_data"]])
        for row in rows:
            writer.writerow([safe_cell(cell) for cell in row])
    writer.writerow([])
    writer.writerow([safe_cell(L["note"])])
    return ("﻿" + out.getvalue()).encode("utf-8")


_PUNCTUATION = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
                "…": "...", "→": "->", "·": "-"}


def _latin1(value: Any) -> str:
    text_value = "" if value is None else str(value)
    for char, replacement in _PUNCTUATION.items():
        text_value = text_value.replace(char, replacement)
    return text_value.encode("latin-1", "replace").decode("latin-1")


def to_pdf(data: dict[str, Any]) -> bytes:
    from fpdf import FPDF

    L = LABELS[data["lang"]]
    metrics, tables = _sections(data)
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, _latin1(L["title"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    for label, value in _header(data):
        pdf.cell(45, 5, _latin1(label))
        pdf.cell(0, 5, _latin1(value), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def table(title, columns, rows, widths):
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, _latin1(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 8)
        for column, w in zip(columns, widths):
            pdf.cell(w, 6, _latin1(column)[:60], border=1)
        pdf.ln()
        pdf.set_font("Helvetica", size=8)
        if not rows:
            pdf.cell(sum(widths), 6, _latin1(L["no_data"]), border=1, new_x="LMARGIN", new_y="NEXT")
        for row in rows:
            for cell, w in zip(row, widths):
                pdf.cell(w, 6, _latin1("-" if cell is None else cell)[:70], border=1)
            pdf.ln()
        pdf.ln(3)

    table(f'{L["section"]} / {L["metric"]}', [L["section"], L["metric"], L["value"]],
          [list(row) for row in metrics], [width * 0.25, width * 0.55, width * 0.2])
    for title, columns, rows in tables:
        table(title, columns, rows, [width / len(columns)] * len(columns))
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, _latin1(L["note"]))
    return bytes(pdf.output())


def export_report(service, principal, args, correlation_id: str | None) -> tuple[bytes, str, str]:
    data = collect(service, principal, args)
    content = to_csv(data) if data["format"] == "csv" else to_pdf(data)
    _audit(service, principal, correlation_id, data["format"])
    f = data["summary"]["filters"]
    filename = f"pef-report-{f['from'][:10]}-{f['to'][:10]}.{data['format']}"
    return content, FORMATS[data["format"]], filename
