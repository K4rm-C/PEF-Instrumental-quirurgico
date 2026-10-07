"""Session-aware assignment over box×family score matrix (greedy or Hungarian)."""

from __future__ import annotations

from typing import Any

import numpy as np

from config import (
    ASSIGN_SOLVER,
    CONF_DISPLAY_REASSIGN,
    CONF_DISPLAY_THRESHOLD,
    CONTEXT_WEIGHT,
    REASSIGN_MAX_GAP,
    REASSIGN_MIN_SCORE,
    TOPK_PERSIST,
    VISUAL_WEIGHT,
    WORKER_PIPELINE,
)

# Cost for non-edges so Hungarian avoids them unless nothing else is left
_INVALID_COST = 1.0e3


def _score_for_family(box: dict[str, Any], family_code: str, class_map: dict[int, dict]) -> float | None:
    """Best class score on this box that maps to family_code."""
    best: float | None = None
    scores = box.get("scores_by_class") or []
    for class_id, meta in class_map.items():
        if meta.get("family_code") != family_code:
            continue
        if class_id < 0 or class_id >= len(scores):
            continue
        s = float(scores[class_id])
        if best is None or s > best:
            best = s
    return best


def _topk_audit(box: dict[str, Any], class_map: dict[int, dict], *, n: int = TOPK_PERSIST) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in (box.get("topk") or [])[:n]:
        meta = class_map.get(t["class_id"])
        if not meta:
            continue
        out.append({
            "family_code": meta.get("family_code"),
            "family_id": meta.get("family_id"),
            "family_name": meta.get("family_name"),
            "score": round(float(t["score"]), 4),
        })
    return out


def _box_audit(
    box: dict[str, Any],
    *,
    class_map: dict[int, dict],
    assigned_code: str | None,
    assigned_meta: dict[str, Any] | None,
    visual: float,
    reason: str,
) -> dict[str, Any]:
    raw_code = box.get("raw_family_code")
    return {
        "xyxy": [round(float(v), 2) for v in box["xyxy"]],
        "yolo_top1": {
            "class_id": int(box["class_id"]),
            "family_code": raw_code,
            "family_id": box.get("raw_family_id"),
            "family_name": box.get("raw_family_name"),
            "score": round(float(box["raw_confidence"]), 4),
        },
        "assigned": {
            "family_code": assigned_code,
            "family_id": (assigned_meta or {}).get("family_id"),
            "family_name": (assigned_meta or {}).get("family_name"),
            "score": round(float(visual), 4) if assigned_code else None,
            "reason": reason,
        },
        "topk": _topk_audit(box, class_map),
    }


def _naming_high(
    *,
    reason: str,
    visual: float,
    raw_conf: float,
    conf_display: float,
    conf_display_reassign: float,
) -> bool:
    """
    kept_raw: visual >= display (0.70).
    reassigned: raw top-1 >= display AND assigned visual >= reassign display (0.60).
    """
    if reason == "reassigned":
        return raw_conf >= conf_display and visual >= conf_display_reassign
    return visual >= conf_display


def _build_edges(
    universe_boxes: list[dict[str, Any]],
    *,
    expected: dict[str, dict[str, Any]],
    class_map: dict[int, dict[str, Any]],
    quotas: dict[str, int],
    reassign_min: float,
    reassign_max_gap: float,
    visual_w: float,
    context_w: float,
) -> list[tuple[float, float, float, int, str]]:
    """Return edges (final, visual, context, box_idx, family_code)."""
    edges: list[tuple[float, float, float, int, str]] = []
    for bi, box in enumerate(universe_boxes):
        raw_code = box["raw_family_code"]
        raw_conf = float(box["raw_confidence"])
        raw_cat = box.get("raw_category_name") or "Type"
        for code, exp_meta in expected.items():
            visual = _score_for_family(box, code, class_map)
            if visual is None:
                continue
            is_raw = code == raw_code
            if not is_raw:
                if visual < reassign_min:
                    continue
                if visual < raw_conf - reassign_max_gap:
                    continue

            context = 0.0
            if quotas.get(code, 0) > 0:
                context += 0.5
            dest_cat = exp_meta.get("category_name") or "Type"
            if dest_cat == raw_cat:
                context += 0.3
            if is_raw:
                context += 0.2
            elif abs(raw_conf - visual) <= reassign_max_gap:
                context += 0.2
            context = min(1.0, context)
            final = visual_w * float(visual) + context_w * context
            edges.append((final, float(visual), context, bi, code))
    return edges


def _match_greedy(
    edges: list[tuple[float, float, float, int, str]],
    *,
    quotas: dict[str, int],
    universe_boxes: list[dict[str, Any]],
) -> tuple[dict[int, str], dict[int, dict[str, Any]]]:
    ordered = sorted(edges, key=lambda e: e[0], reverse=True)
    assigned_box: dict[int, str] = {}
    filled: dict[str, int] = {code: 0 for code in quotas}
    edge_meta: dict[int, dict[str, Any]] = {}

    for final, visual, context, bi, code in ordered:
        if bi in assigned_box:
            continue
        if filled[code] >= quotas[code]:
            continue
        assigned_box[bi] = code
        filled[code] += 1
        raw_code = universe_boxes[bi]["raw_family_code"]
        edge_meta[bi] = {
            "visual_score": visual,
            "context_score": context,
            "final_score": final,
            "resolution_reason": "kept_raw" if code == raw_code else "reassigned",
        }
    return assigned_box, edge_meta


def _match_hungarian(
    edges: list[tuple[float, float, float, int, str]],
    *,
    quotas: dict[str, int],
    universe_boxes: list[dict[str, Any]],
) -> tuple[dict[int, str], dict[int, dict[str, Any]]]:
    """
    Expand each family into expected_quantity slots; minimize -final_score.
    Reject pairings that fell on INVALID_COST (no real edge).
    """
    from scipy.optimize import linear_sum_assignment

    n_boxes = len(universe_boxes)
    if n_boxes == 0 or not edges:
        return {}, {}

    slots: list[str] = []
    for code, q in quotas.items():
        for _ in range(max(0, int(q))):
            slots.append(code)
    if not slots:
        return {}, {}

    # Best edge meta per (box, family)
    best: dict[tuple[int, str], tuple[float, float, float]] = {}
    for final, visual, context, bi, code in edges:
        key = (bi, code)
        prev = best.get(key)
        if prev is None or final > prev[0]:
            best[key] = (final, visual, context)

    n_slots = len(slots)
    cost = np.full((n_boxes, n_slots), _INVALID_COST, dtype=np.float64)
    for bi in range(n_boxes):
        for sj, code in enumerate(slots):
            meta = best.get((bi, code))
            if meta is None:
                continue
            final, _visual, _context = meta
            cost[bi, sj] = -float(final)

    row_ind, col_ind = linear_sum_assignment(cost)
    assigned_box: dict[int, str] = {}
    edge_meta: dict[int, dict[str, Any]] = {}
    for bi, sj in zip(row_ind.tolist(), col_ind.tolist()):
        if cost[bi, sj] >= _INVALID_COST * 0.5:
            continue
        code = slots[sj]
        meta = best.get((bi, code))
        if meta is None:
            continue
        final, visual, context = meta
        # One box → one family (slots of same family are interchangeable)
        if bi in assigned_box:
            continue
        assigned_box[bi] = code
        raw_code = universe_boxes[bi]["raw_family_code"]
        edge_meta[bi] = {
            "visual_score": visual,
            "context_score": context,
            "final_score": final,
            "resolution_reason": "kept_raw" if code == raw_code else "reassigned",
        }
    return assigned_box, edge_meta


def assign_boxes(
    raw_boxes: list[dict[str, Any]],
    *,
    expected: dict[str, dict[str, Any]],
    class_map: dict[int, dict[str, Any]],
    conf_display: float = CONF_DISPLAY_THRESHOLD,
    conf_display_reassign: float = CONF_DISPLAY_REASSIGN,
    reassign_min: float = REASSIGN_MIN_SCORE,
    reassign_max_gap: float = REASSIGN_MAX_GAP,
    visual_w: float = VISUAL_WEIGHT,
    context_w: float = CONTEXT_WEIGHT,
    solver: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """
    Filter to expected universe, match quotas (greedy|hungarian), Type degrade.

    Returns refined boxes, counts_by_family, assignment_metrics.
    """
    solver_name = (solver or ASSIGN_SOLVER or "hungarian").strip().lower()
    if solver_name not in {"greedy", "hungarian"}:
        solver_name = "hungarian"

    universe_boxes: list[dict[str, Any]] = []
    for box in raw_boxes:
        class_id = int(box["class_id"])
        meta = class_map.get(class_id)
        if meta is None:
            continue
        raw_code = meta.get("family_code")
        in_universe = raw_code in expected
        if not in_universe:
            for code in expected:
                s = _score_for_family(box, code, class_map)
                if s is not None and s >= reassign_min:
                    in_universe = True
                    break
        if not in_universe:
            continue
        universe_boxes.append({
            **box,
            "raw_family_code": raw_code,
            "raw_family_id": meta.get("family_id"),
            "raw_family_name": meta.get("family_name"),
            "raw_category_name": meta.get("category_name") or "Type",
            "raw_confidence": float(box["confidence"]),
        })

    raw_tallies: dict[str, list[float]] = {code: [] for code in expected}
    for box in universe_boxes:
        code = box["raw_family_code"]
        if code in raw_tallies:
            raw_tallies[code].append(float(box["raw_confidence"]))

    quotas = {code: int(meta.get("expected_quantity") or 0) for code, meta in expected.items()}
    edges = _build_edges(
        universe_boxes,
        expected=expected,
        class_map=class_map,
        quotas=quotas,
        reassign_min=reassign_min,
        reassign_max_gap=reassign_max_gap,
        visual_w=visual_w,
        context_w=context_w,
    )

    if solver_name == "greedy":
        assigned_box, edge_meta = _match_greedy(
            edges, quotas=quotas, universe_boxes=universe_boxes
        )
    else:
        assigned_box, edge_meta = _match_hungarian(
            edges, quotas=quotas, universe_boxes=universe_boxes
        )

    refined_boxes: list[dict[str, Any]] = []
    refined_tallies: dict[str, list[float]] = {code: [] for code in expected}
    boxes_by_family: dict[str, list[dict[str, Any]]] = {code: [] for code in expected}
    type_degraded_audits: list[dict[str, Any]] = []
    reassigned_in = {code: 0 for code in expected}
    reassigned_out = {code: 0 for code in expected}
    type_only = 0

    for bi, box in enumerate(universe_boxes):
        raw_code = box["raw_family_code"]
        raw_conf = float(box["raw_confidence"])
        meta_edge = edge_meta.get(bi)
        topk_list = _topk_audit(box, class_map)
        if meta_edge is None:
            label = box.get("raw_category_name") or "Type"
            audit = _box_audit(
                box,
                class_map=class_map,
                assigned_code=None,
                assigned_meta=None,
                visual=raw_conf,
                reason="type_degraded",
            )
            type_degraded_audits.append(audit)
            refined_boxes.append({
                "xyxy": box["xyxy"],
                "raw": {
                    "class_id": box["class_id"],
                    "family_code": raw_code,
                    "family_id": box.get("raw_family_id"),
                    "family_name": box.get("raw_family_name"),
                    "confidence": raw_conf,
                    "topk": topk_list,
                },
                "refined": {
                    "family_code": None,
                    "family_id": None,
                    "family_name": None,
                    "category_name": box.get("raw_category_name"),
                    "label": label,
                    "visual_score": raw_conf,
                    "context_score": 0.0,
                    "final_score": raw_conf,
                    "resolution_reason": "type_degraded",
                    "high_confidence": False,
                },
                "label": label,
                "confidence": raw_conf,
                "high_confidence": False,
                "family_code": None,
                "family_id": None,
                "family_name": None,
                "category_name": box.get("raw_category_name"),
                "resolution_reason": "type_degraded",
                "audit": audit,
            })
            type_only += 1
            continue

        code = assigned_box[bi]
        exp = expected[code]
        visual = meta_edge["visual_score"]
        reason = meta_edge["resolution_reason"]
        if reason == "reassigned":
            reassigned_in[code] += 1
            if raw_code in reassigned_out:
                reassigned_out[raw_code] += 1
        high = _naming_high(
            reason=reason,
            visual=visual,
            raw_conf=raw_conf,
            conf_display=conf_display,
            conf_display_reassign=conf_display_reassign,
        )
        family_label = exp.get("family_name") or code
        label = family_label if high else (exp.get("category_name") or "Type")
        refined_tallies[code].append(visual)
        audit = _box_audit(
            box,
            class_map=class_map,
            assigned_code=code,
            assigned_meta=exp,
            visual=visual,
            reason=reason,
        )
        boxes_by_family[code].append(audit)
        refined_boxes.append({
            "xyxy": box["xyxy"],
            "raw": {
                "class_id": box["class_id"],
                "family_code": raw_code,
                "family_id": box.get("raw_family_id"),
                "family_name": box.get("raw_family_name"),
                "confidence": raw_conf,
                "topk": topk_list,
            },
            "refined": {
                "family_code": code,
                "family_id": exp.get("family_id"),
                "family_name": family_label,
                "category_name": exp.get("category_name"),
                "label": label,
                "visual_score": visual,
                "context_score": meta_edge["context_score"],
                "final_score": meta_edge["final_score"],
                "resolution_reason": reason,
                "high_confidence": high,
            },
            "label": label,
            "confidence": visual,
            "high_confidence": high,
            "family_code": code,
            "family_id": exp.get("family_id"),
            "family_name": family_label,
            "category_name": exp.get("category_name"),
            "resolution_reason": reason,
            "audit": audit,
        })

    counts: dict[str, dict[str, Any]] = {}
    extras_raw = extras_ref = missing_raw = missing_ref = 0
    match_raw = match_ref = 0
    for code, exp in expected.items():
        eq = int(exp.get("expected_quantity") or 0)
        raw_confs = raw_tallies.get(code) or []
        ref_confs = refined_tallies.get(code) or []
        raw_n = len(raw_confs)
        ref_n = len(ref_confs)
        if raw_n == eq:
            match_raw += 1
        if ref_n == eq:
            match_ref += 1
        if raw_n > eq:
            extras_raw += raw_n - eq
        if ref_n > eq:
            extras_ref += ref_n - eq
        if raw_n < eq:
            missing_raw += eq - raw_n
        if ref_n < eq:
            missing_ref += eq - ref_n
        counts[code] = {
            "family_id": exp.get("family_id"),
            "family_code": code,
            "family_name": exp.get("family_name"),
            "category_name": exp.get("category_name"),
            "expected_quantity": eq,
            "raw_detected_quantity": raw_n,
            "detected_quantity": ref_n,
            "difference": ref_n - eq,
            "box_confidences": [round(c, 4) for c in ref_confs],
            "best_confidence": max(ref_confs) if ref_confs else None,
            "weakest_confidence": min(ref_confs) if ref_confs else None,
            "avg_confidence": (sum(ref_confs) / ref_n) if ref_n else None,
            "reassigned_in": reassigned_in.get(code, 0),
            "reassigned_out": reassigned_out.get(code, 0),
            "pipeline": WORKER_PIPELINE,
            "solver": solver_name,
            "boxes": boxes_by_family.get(code) or [],
            "held": False,
        }

    n_fam = max(len(expected), 1)
    metrics = {
        "pipeline": WORKER_PIPELINE,
        "solver": solver_name,
        "extras_raw": extras_raw,
        "extras_refined": extras_ref,
        "missing_raw": missing_raw,
        "missing_refined": missing_ref,
        "reassign_count": sum(reassigned_in.values()),
        "type_degrade_count": type_only,
        "family_match_rate_raw": match_raw / n_fam,
        "family_match_rate_refined": match_ref / n_fam,
        "boxes_raw_universe": len(universe_boxes),
        "boxes_refined_named": sum(1 for b in refined_boxes if b.get("family_code")),
        "type_degraded_boxes": type_degraded_audits,
    }
    return refined_boxes, counts, metrics
