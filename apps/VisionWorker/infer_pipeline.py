"""Custom YOLO26 detect forward + NMS that carries per-class score rows."""

from __future__ import annotations

from typing import Any

import numpy as np
import torch

from config import BOX_RECALL_CONF, IMGSZ, MAX_DET, NMS_IOU, TOPK


def _letterbox(image_bgr: np.ndarray, imgsz: int = IMGSZ) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize+pad to square; return rgb_chw-ready image, scale, (pad_w, pad_h)."""
    h0, w0 = image_bgr.shape[:2]
    r = min(imgsz / h0, imgsz / w0)
    new_w, new_h = int(round(w0 * r)), int(round(h0 * r))
    import cv2

    resized = cv2.resize(image_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_w = imgsz - new_w
    pad_h = imgsz - new_h
    left, right = pad_w // 2, pad_w - pad_w // 2
    top, bottom = pad_h // 2, pad_h - pad_h // 2
    padded = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
    )
    return padded, r, (left, top)


def _xywh_to_xyxy(box: np.ndarray) -> np.ndarray:
    """box (N,4) center-x,y,w,h → xyxy."""
    out = np.empty_like(box)
    out[:, 0] = box[:, 0] - box[:, 2] / 2
    out[:, 1] = box[:, 1] - box[:, 3] / 2
    out[:, 2] = box[:, 0] + box[:, 2] / 2
    out[:, 3] = box[:, 1] + box[:, 3] / 2
    return out


def _nms_xyxy(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> list[int]:
    """Classic greedy NMS; returns kept indices."""
    if boxes.size == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0 and len(keep) < MAX_DET:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[i] + areas[rest] - inter
        iou = np.where(union > 0, inter / union, 0.0)
        order = rest[iou <= iou_thres]
    return keep


def _scale_boxes_to_orig(
    xyxy: np.ndarray, *, r: float, pad: tuple[int, int], orig_shape: tuple[int, int]
) -> np.ndarray:
    left, top = pad
    h0, w0 = orig_shape
    out = xyxy.copy()
    out[:, [0, 2]] -= left
    out[:, [1, 3]] -= top
    out[:, :4] /= max(r, 1e-9)
    out[:, [0, 2]] = out[:, [0, 2]].clip(0, w0)
    out[:, [1, 3]] = out[:, [1, 3]].clip(0, h0)
    return out


def detect_with_class_scores(
    model,
    frame_bgr: np.ndarray,
    *,
    recall_conf: float = BOX_RECALL_CONF,
    iou_thres: float = NMS_IOU,
    topk: int = TOPK,
) -> list[dict[str, Any]]:
    """
    Run YOLO26 detect forward and NMS, keeping the full class-score row per kept box.

    Returns list of:
      xyxy, class_id, confidence, scores_by_class (list[float] len nc),
      topk (list[{class_id, score}])
    """
    device = next(model.model.parameters()).device
    padded, r, pad = _letterbox(frame_bgr, IMGSZ)
    rgb = padded[:, :, ::-1].transpose(2, 0, 1)
    im = torch.from_numpy(np.ascontiguousarray(rgb)).float()[None] / 255.0
    im = im.to(device)

    model.model.eval()
    with torch.no_grad():
        out = model.model(im)

    # Ultralytics DetectionModel: (y, preds) when not export; y = (1, 4+nc, A)
    if isinstance(out, (list, tuple)) and len(out) >= 1 and torch.is_tensor(out[0]):
        y = out[0]
    elif torch.is_tensor(out):
        y = out
    else:
        raise RuntimeError(f"Unexpected model output type: {type(out)}")

    if y.ndim != 3 or y.shape[0] != 1:
        raise RuntimeError(f"Unexpected prediction shape: {tuple(y.shape)}")

    pred = y[0].detach().float().cpu().numpy()  # (4+nc, A)
    nc = pred.shape[0] - 4
    if nc < 1:
        return []

    boxes_xywh = pred[:4, :].T  # (A, 4)
    cls_scores = pred[4:, :].T  # (A, nc)
    # Decoded boxes from Ultralytics _inference are typically xywh in letterbox space
    boxes_xyxy = _xywh_to_xyxy(boxes_xywh)

    top1_conf = cls_scores.max(axis=1)
    top1_cls = cls_scores.argmax(axis=1)
    mask = top1_conf >= recall_conf
    if not np.any(mask):
        return []

    boxes_xyxy = boxes_xyxy[mask]
    cls_scores = cls_scores[mask]
    top1_conf = top1_conf[mask]
    top1_cls = top1_cls[mask]

    keep = _nms_xyxy(boxes_xyxy, top1_conf, iou_thres)
    if not keep:
        return []

    boxes_xyxy = _scale_boxes_to_orig(
        boxes_xyxy[keep], r=r, pad=pad, orig_shape=frame_bgr.shape[:2]
    )
    cls_scores = cls_scores[keep]
    top1_conf = top1_conf[keep]
    top1_cls = top1_cls[keep]

    names = model.names if isinstance(model.names, dict) else {i: str(i) for i in range(nc)}
    results: list[dict[str, Any]] = []
    k = min(topk, nc)
    for i in range(len(keep)):
        row = cls_scores[i]
        order = np.argsort(row)[::-1][:k]
        topk_list = [
            {
                "class_id": int(cid),
                "score": float(row[cid]),
                "model_name": names.get(int(cid), str(int(cid))),
            }
            for cid in order
        ]
        results.append({
            "xyxy": [float(v) for v in boxes_xyxy[i].tolist()],
            "class_id": int(top1_cls[i]),
            "confidence": float(top1_conf[i]),
            "scores_by_class": [float(v) for v in row.tolist()],
            "topk": topk_list,
            "model_name": names.get(int(top1_cls[i]), str(int(top1_cls[i]))),
        })
    return results
