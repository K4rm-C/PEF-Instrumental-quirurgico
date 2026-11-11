"""Physical total of the last capture: instruments outside the kit, non-instruments, duplicates.

Synthetic detections (same shape as infer_pipeline.detect_with_class_scores); no YOLO model.
"""

from assign import assign_boxes
from config import OUT_OF_KIT_MIN_CONF
from inference import SessionState, _apply_hold, _category_summary, _out_of_kit_summary, _public_box

# Model classes registered as instrument families (class_map from the active model).
CLASS_MAP = {
    0: {'family_id': 'f-a', 'family_code': 'A', 'family_name': 'Kelly forceps', 'category_name': 'Hemostasis'},
    1: {'family_id': 'f-b', 'family_code': 'B', 'family_name': 'Mayo scissors', 'category_name': 'Cutting'},
    2: {'family_id': 'f-x', 'family_code': 'X', 'family_name': 'Bone rongeur', 'category_name': 'Cutting'},
}
# The session kit only expects families A and B.
EXPECTED = {
    'A': {'family_id': 'f-a', 'family_code': 'A', 'family_name': 'Kelly forceps',
          'category_name': 'Hemostasis', 'expected_quantity': 1},
    'B': {'family_id': 'f-b', 'family_code': 'B', 'family_name': 'Mayo scissors',
          'category_name': 'Cutting', 'expected_quantity': 1},
}


def _det(class_id, conf, x, nc=4):
    scores = [0.01] * nc
    scores[class_id] = conf
    return {'xyxy': [x, 10.0, x + 40.0, 60.0], 'class_id': class_id, 'confidence': conf,
            'scores_by_class': scores, 'topk': [{'class_id': class_id, 'score': conf}]}


def _assign(raw):
    return assign_boxes(raw, expected=EXPECTED, class_map=CLASS_MAP)


def test_instrument_of_a_family_outside_the_kit_is_kept_and_counted():
    refined, counts, metrics = _assign([_det(0, 0.9, 0), _det(1, 0.9, 100), _det(2, 0.8, 200)])
    assert len(refined) == 3  # physical total does not depend on the kit families
    foreign = [b for b in refined if b['resolution_reason'] == 'out_of_kit']
    assert len(foreign) == 1
    assert foreign[0]['family_code'] is None  # never assigned to a kit quota
    assert foreign[0]['out_of_kit'] == {'family_code': 'X', 'family_id': 'f-x', 'family_name': 'Bone rongeur'}
    assert sum(c['detected_quantity'] for c in counts.values()) == 2  # kit tallies unchanged
    assert metrics['out_of_kit_count'] == 1


def test_low_confidence_foreign_box_is_not_counted_as_an_instrument():
    refined, _, metrics = _assign([_det(0, 0.9, 0), _det(2, OUT_OF_KIT_MIN_CONF - 0.05, 200)])
    assert len(refined) == 1
    assert metrics['out_of_kit_low_conf_dropped'] == 1


def test_unmapped_model_class_is_not_counted():
    """Class 3 is not registered as an instrument family: not known to be an instrument."""
    refined, _, metrics = _assign([_det(0, 0.9, 0), _det(3, 0.95, 300)])
    assert len(refined) == 1
    assert metrics['unmapped_class_dropped'] == 1


def test_out_of_kit_boxes_stay_out_of_kit_category_quotas():
    refined, counts, _ = _assign([_det(1, 0.9, 100), _det(2, 0.8, 200)])
    boxes = [_public_box(b) for b in refined]
    summary = _category_summary(expected=EXPECTED, counts=counts, boxes=boxes)
    assert summary['Cutting']['unnamed_raw'] == 0  # the rongeur does not fill a Cutting slot
    assert _out_of_kit_summary(boxes) == [{
        'family_code': 'X', 'family_id': 'f-x', 'family_name': 'Bone rongeur', 'category_name': 'Cutting',
        'count': 1, 'best_confidence': 0.8,
    }]


def _frame(state, raw, video_t):
    refined, counts, _ = _assign(raw)
    return _apply_hold(state, counts=counts, public_boxes=[_public_box(b) for b in refined], video_t=video_t)


def test_reclassified_instrument_is_not_counted_twice():
    """A is seen for a few frames, then YOLO labels the same piece as the foreign family X."""
    state = SessionState(session_id='s', expected=EXPECTED, class_map=CLASS_MAP)
    for i in range(4):
        _frame(state, [_det(0, 0.9, 0), _det(1, 0.9, 100)], video_t=i * 0.2)
    counts, boxes = _frame(state, [_det(2, 0.85, 0), _det(1, 0.9, 100)], video_t=1.0)
    assert len(boxes) == 2  # the held A box overlaps the live X box: same object
    assert counts['A']['held'] is True  # the family tally hold itself is unchanged


def test_hold_still_covers_a_briefly_occluded_instrument():
    state = SessionState(session_id='s', expected=EXPECTED, class_map=CLASS_MAP)
    for i in range(4):
        _frame(state, [_det(0, 0.9, 0), _det(1, 0.9, 100)], video_t=i * 0.2)
    _, boxes = _frame(state, [_det(1, 0.9, 100)], video_t=1.0)  # A hidden by a hand
    assert len(boxes) == 2
