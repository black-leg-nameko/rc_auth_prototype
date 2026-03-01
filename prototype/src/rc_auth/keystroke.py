from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

import numpy as np


@dataclass(frozen=True)
class TypingProfile:
    dwell_mean: float
    dwell_std: float
    flight_mean: float
    flight_std: float
    backspace_prob: float
    repeat_prob: float


@dataclass(frozen=True)
class KeystrokeEvent:
    dwell_ms: float
    flight_ms: float
    key_code: int
    is_backspace: bool


def generate_typing_events(
    n_events: int,
    profile: TypingProfile,
    rng: np.random.Generator,
) -> List[KeystrokeEvent]:
    events: List[KeystrokeEvent] = []
    last_key = int(rng.integers(0, 26))

    for _ in range(n_events):
        dwell = max(15.0, rng.normal(profile.dwell_mean, profile.dwell_std))
        flight = max(10.0, rng.normal(profile.flight_mean, profile.flight_std))

        is_repeat = rng.random() < profile.repeat_prob
        key_code = last_key if is_repeat else int(rng.integers(0, 26))
        is_backspace = rng.random() < profile.backspace_prob
        if is_backspace:
            key_code = 8

        events.append(
            KeystrokeEvent(
                dwell_ms=float(dwell),
                flight_ms=float(flight),
                key_code=key_code,
                is_backspace=is_backspace,
            )
        )
        last_key = key_code

    return events


def extract_window_features(events: Iterable[KeystrokeEvent]) -> np.ndarray:
    event_list = list(events)
    if not event_list:
        raise ValueError("events must not be empty")

    dwell = np.array([e.dwell_ms for e in event_list], dtype=np.float64)
    flight = np.array([e.flight_ms for e in event_list], dtype=np.float64)
    keys = np.array([e.key_code for e in event_list], dtype=np.int32)
    backspaces = np.array([1.0 if e.is_backspace else 0.0 for e in event_list], dtype=np.float64)

    if len(keys) >= 2:
        repeats = float(np.mean(keys[1:] == keys[:-1]))
    else:
        repeats = 0.0

    total_time_ms = float(np.sum(dwell + flight))
    keys_per_sec = (1000.0 * len(event_list) / total_time_ms) if total_time_ms > 0 else 0.0

    features = np.array(
        [
            float(np.mean(dwell)),
            float(np.std(dwell)),
            float(np.mean(flight)),
            float(np.std(flight)),
            float(keys_per_sec),
            float(np.mean(backspaces)),
            repeats,
        ],
        dtype=np.float64,
    )

    return features


def build_feature_stream(
    events: List[KeystrokeEvent],
    window_size: int = 40,
    step_size: int = 20,
) -> np.ndarray:
    if window_size <= 0:
        raise ValueError("window_size must be > 0")
    if step_size <= 0:
        raise ValueError("step_size must be > 0")
    if len(events) < window_size:
        raise ValueError("not enough events for one window")

    windows: List[np.ndarray] = []
    for start in range(0, len(events) - window_size + 1, step_size):
        window = events[start : start + window_size]
        windows.append(extract_window_features(window))

    return np.vstack(windows)

