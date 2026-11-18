"""MS-11 (R11): reproducible synthetic sequences for the instrument tracker (no model, no randomness).

Each scenario has a known ground truth (number of objects, which object is which), so the assertions measure
identity stability, recovery after occlusion, class hysteresis, confidence handling and ROI events.
"""
import math

import pytest

from tracking import InstrumentTracker, TrackerConfig, normalize_roi

FPS = 6.0          # processed frames per second (30 fps video with VISION_FRAME_STRIDE=5)


def det(x, y, family='A', conf=0.9, w=60, h=20):
    return {'xyxy': [x, y, x + w, y + h], 'family_code': family, 'confidence': conf}


def run(tracker, frames):
    """frames: list of detection lists; returns the list of id lists per frame."""
    out = []
    for i, dets in enumerate(frames):
        out.append(tracker.update(dets, t=i / FPS, frame=i * 5))
    return out


def test_three_static_instruments_keep_three_stable_ids():
    tracker = InstrumentTracker(TrackerConfig(min_hits=3))
    frames = [[det(10 + i, 10), det(200, 50 + i, 'B'), det(400 - i, 120, 'C')] for i in range(30)]
    ids = run(tracker, frames)
    assert all(row == ids[-1] for row in ids)                      # no re-numbering between frames
    s = tracker.summary(final_frame_total=3)
    assert s['unique_confirmed_tracks'] == 3 and s['active_confirmed_tracks'] == 3
    assert s['active_tracks_match_final_frame'] is True
    assert s['stats']['lost'] == 0 and s['stats']['class_changes'] == 0


def test_fast_motion_without_overlap_keeps_the_id():
    """60 px wide box moving 70 px per processed frame: consecutive boxes never overlap (IoU = 0)."""
    tracker = InstrumentTracker(TrackerConfig(min_hits=2))
    frames = [[det(20 + 70 * i, 100)] for i in range(12)]
    ids = run(tracker, frames)
    assert len({row[0] for row in ids}) == 1
    assert tracker.summary()['unique_confirmed_tracks'] == 1


def test_brief_occlusion_shorter_than_lost_window_is_invisible():
    tracker = InstrumentTracker(TrackerConfig(min_hits=2, lost_after_s=0.4))
    frames = [[det(100, 100)] for _ in range(6)] + [[]] + [[det(100, 100)] for _ in range(6)]   # 1 frame = 0.17 s gap
    ids = run(tracker, frames)
    assert {row[0] for row in ids if row} == {1}
    assert tracker.summary()['stats']['lost'] == 0


def test_occlusion_longer_than_lost_window_recovers_the_same_id():
    tracker = InstrumentTracker(TrackerConfig(min_hits=2, lost_after_s=0.4, forget_after_s=3.0))
    gap = [[] for _ in range(6)]                                                      # 1 s: hand over the tray
    frames = [[det(100, 100)] for _ in range(6)] + gap + [[det(104, 102)] for _ in range(6)]
    ids = run(tracker, frames)
    assert {row[0] for row in ids if row} == {1}
    s = tracker.summary(include_events=True)
    assert s['stats']['lost'] == 1 and s['stats']['recovered'] == 1
    recovered = [e for e in s['events'] if e['event'] == 'recovered'][0]
    assert recovered['track_id'] == 1 and recovered['gap_s'] >= 1.0


def test_object_gone_longer_than_forget_window_gets_a_new_id_and_is_terminated():
    tracker = InstrumentTracker(TrackerConfig(min_hits=2, lost_after_s=0.4, forget_after_s=1.0))
    frames = [[det(100, 100)] for _ in range(6)] + [[] for _ in range(12)] + [[det(100, 100)] for _ in range(6)]
    ids = run(tracker, frames)
    present = [row[0] for row in ids if row]
    assert present[0] == 1 and present[-1] == 2
    assert tracker.summary()['stats']['terminated'] == 1


def test_single_frame_class_flips_do_not_change_the_class_but_a_sustained_change_does():
    tracker = InstrumentTracker(TrackerConfig(min_hits=2, ema_alpha=0.35, class_switch_margin=0.15))
    flips = [det(100, 100, 'B' if i in (5, 9) else 'A') for i in range(14)]
    run(tracker, [[d] for d in flips])
    assert tracker.tracks[0].family == 'A' and tracker.summary()['stats']['class_changes'] == 0
    run_more = [[det(100, 100, 'B')] for _ in range(10)]
    for i, dets in enumerate(run_more, start=14):
        tracker.update(dets, t=i / FPS, frame=i * 5)
    s = tracker.summary(include_events=True)
    assert tracker.tracks[0].family == 'B' and s['stats']['class_changes'] == 1
    change = [e for e in s['events'] if e['event'] == 'class_change'][0]
    assert change['from_family'] == 'A' and change['to_family'] == 'B'
    assert {row for row in [tracker.tracks[0].id]} == {1}                         # same object, same ID


def test_low_confidence_detection_never_creates_an_object_but_can_follow_one():
    tracker = InstrumentTracker(TrackerConfig(min_hits=2, min_create_conf=0.30))
    ids = run(tracker, [[det(300, 300, conf=0.12)] for _ in range(8)])
    assert all(row == [None] for row in ids) and tracker.summary()['unique_confirmed_tracks'] == 0
    tracker = InstrumentTracker(TrackerConfig(min_hits=2, min_create_conf=0.30))
    ids = run(tracker, [[det(100, 100, conf=0.9)] for _ in range(4)] + [[det(101, 100, conf=0.15)] for _ in range(4)])
    assert {row[0] for row in ids} == {1}


def test_two_neighbouring_instruments_crossing_do_not_swap_ids():
    """Two confident instruments of different classes pass each other side by side (overlap < 0.5)."""
    tracker = InstrumentTracker(TrackerConfig(min_hits=2))
    frames = [[det(50 + 25 * i, 100, 'A'), det(400 - 25 * i, 112, 'B')] for i in range(14)]
    ids = run(tracker, frames)
    first_a, first_b = ids[0]
    assert all(row == [first_a, first_b] for row in ids)
    assert tracker.summary()['unique_confirmed_tracks'] == 2


def test_tracking_never_counts_more_objects_than_are_in_the_frame():
    """Fragmentation (an object lost and re-created after the forget window) raises unique_confirmed_tracks,
    but the tracker never turns that into a count: the active tracks equal the objects in view."""
    tracker = InstrumentTracker(TrackerConfig(min_hits=2, lost_after_s=0.2, forget_after_s=0.5))
    frames = []
    for cycle in range(4):
        frames += [[det(100, 100), det(300, 100, 'B')] for _ in range(4)] + [[det(300, 100, 'B')] for _ in range(6)]
    run(tracker, frames)
    s = tracker.summary(final_frame_total=1)
    assert s['unique_confirmed_tracks'] > 2           # the re-appearing object was re-identified as new
    assert s['active_confirmed_tracks'] == 1 and s['active_tracks_match_final_frame'] is True
    assert 'note' in s and 'not counts' in s['note']


def test_roi_entry_and_exit_events():
    roi = normalize_roi({'x': 200, 'y': 0, 'w': 200, 'h': 400, 'frame_w': 640, 'frame_h': 480}, 640, 480)
    tracker = InstrumentTracker(TrackerConfig(min_hits=2), roi=roi)
    frames = [[det(40 + 30 * i, 100)] for i in range(20)]           # centre crosses x=200 and later x=400
    run(tracker, frames)
    kinds = [e['event'] for e in tracker.summary(include_events=True)['events']]
    assert kinds.count('enter_roi') == 1 and kinds.count('exit_roi') == 1
    assert kinds.index('enter_roi') < kinds.index('exit_roi')


def test_roi_is_scaled_to_the_analysed_frame_and_ignored_when_incomplete():
    assert normalize_roi({'x': 100, 'y': 50, 'w': 100, 'h': 50, 'frame_w': 1280, 'frame_h': 720}, 640, 360) == (50, 25, 100, 50)
    assert normalize_roi({'x': 1, 'y': 1}, 640, 360) is None
    assert normalize_roi(None, 640, 360) is None


def test_event_log_is_bounded_and_counts_dropped_events():
    tracker = InstrumentTracker(TrackerConfig(min_hits=1, lost_after_s=0.1, forget_after_s=0.2, max_events=5))
    run(tracker, [[det(100 * (i % 7), 100)] if i % 2 == 0 else [] for i in range(40)])
    s = tracker.summary()
    assert s['events_recorded'] == 5 and s['events_dropped'] > 0


def test_summary_reports_the_configuration_for_reproducibility():
    s = InstrumentTracker(TrackerConfig(min_hits=4)).summary()
    assert s['config']['min_hits'] == 4 and s['version'].startswith('ms11')


@pytest.mark.parametrize('jitter', [0, 3, 6])
def test_jittery_detections_of_a_static_object_stay_one_track(jitter):
    tracker = InstrumentTracker(TrackerConfig(min_hits=2))
    frames = [[det(200 + jitter * math.sin(i), 150 + jitter * math.cos(i))] for i in range(30)]
    ids = run(tracker, frames)
    assert len({row[0] for row in ids}) == 1
