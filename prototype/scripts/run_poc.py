#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rc_auth import RCAuthConfig, RCContinuousAuthenticator, TypingProfile, build_feature_stream, generate_typing_events


@dataclass(frozen=True)
class DatasetSplit:
    train_genuine: Sequence[np.ndarray]
    train_impostor: Sequence[np.ndarray]
    calib_genuine: Sequence[np.ndarray]
    calib_impostor: Sequence[np.ndarray]
    test_genuine: Sequence[np.ndarray]
    test_impostor: Sequence[np.ndarray]


def main() -> None:
    parser = argparse.ArgumentParser(description="RC continuous authentication PoC")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--events-per-seq", type=int, default=320)
    parser.add_argument("--num-train", type=int, default=24)
    parser.add_argument("--num-calib", type=int, default=8)
    parser.add_argument("--num-test", type=int, default=8)
    parser.add_argument("--window-size", type=int, default=40)
    parser.add_argument("--step-size", type=int, default=20)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)

    owner_profile = TypingProfile(
        dwell_mean=95.0,
        dwell_std=12.0,
        flight_mean=110.0,
        flight_std=16.0,
        backspace_prob=0.04,
        repeat_prob=0.02,
    )
    impostor_profile = TypingProfile(
        dwell_mean=145.0,
        dwell_std=24.0,
        flight_mean=185.0,
        flight_std=30.0,
        backspace_prob=0.18,
        repeat_prob=0.11,
    )

    split = build_dataset_split(
        rng=rng,
        owner_profile=owner_profile,
        impostor_profile=impostor_profile,
        events_per_seq=args.events_per_seq,
        num_train=args.num_train,
        num_calib=args.num_calib,
        num_test=args.num_test,
        window_size=args.window_size,
        step_size=args.step_size,
    )

    input_dim = split.train_genuine[0].shape[1]
    model = RCContinuousAuthenticator(
        input_dim=input_dim,
        config=RCAuthConfig(
            reservoir_dim=256,
            sparsity=0.92,
            spectral_radius=0.9,
            leak_rate=0.25,
            ridge_lambda=1e-3,
            seed=args.seed,
        ),
    )

    model.fit(split.train_genuine, split.train_impostor)
    threshold = model.calibrate_threshold(split.calib_genuine, split.calib_impostor)
    frr, far = model.evaluate(split.test_genuine, split.test_impostor)

    print("== RC Keystroke PoC ==")
    print(f"threshold: {threshold:.3f}")
    print(f"test FRR (false reject): {frr:.3%}")
    print(f"test FAR (false accept): {far:.3%}")

    session_seq, takeover_index = build_takeover_session(
        rng=rng,
        owner_profile=owner_profile,
        impostor_profile=impostor_profile,
        events_per_part=args.events_per_seq,
        window_size=args.window_size,
        step_size=args.step_size,
    )
    scores = model.score_sequence(session_seq)
    trigger_index = find_consecutive_low_scores(scores, threshold, consecutive=3)

    print("\n== Continuous Session Demo ==")
    print(f"session windows: {len(scores)}")
    print(f"takeover starts at window index: {takeover_index}")
    if trigger_index is None:
        print("trigger: not detected")
    else:
        delay = trigger_index - takeover_index + 1
        print(f"trigger index: {trigger_index}")
        print(f"detection delay (windows): {max(delay, 0)}")

    print("\nscore preview:")
    preview_scores(scores, threshold, takeover_index)


def build_dataset_split(
    rng: np.random.Generator,
    owner_profile: TypingProfile,
    impostor_profile: TypingProfile,
    events_per_seq: int,
    num_train: int,
    num_calib: int,
    num_test: int,
    window_size: int,
    step_size: int,
) -> DatasetSplit:
    n_total = num_train + num_calib + num_test
    genuine = build_sequences(
        profile=owner_profile,
        n_sequences=n_total,
        events_per_seq=events_per_seq,
        window_size=window_size,
        step_size=step_size,
        rng=rng,
    )
    impostor = build_sequences(
        profile=impostor_profile,
        n_sequences=n_total,
        events_per_seq=events_per_seq,
        window_size=window_size,
        step_size=step_size,
        rng=rng,
    )

    return DatasetSplit(
        train_genuine=genuine[:num_train],
        train_impostor=impostor[:num_train],
        calib_genuine=genuine[num_train : num_train + num_calib],
        calib_impostor=impostor[num_train : num_train + num_calib],
        test_genuine=genuine[num_train + num_calib :],
        test_impostor=impostor[num_train + num_calib :],
    )


def build_sequences(
    profile: TypingProfile,
    n_sequences: int,
    events_per_seq: int,
    window_size: int,
    step_size: int,
    rng: np.random.Generator,
) -> List[np.ndarray]:
    sequences: List[np.ndarray] = []
    for _ in range(n_sequences):
        events = generate_typing_events(events_per_seq, profile, rng)
        feature_stream = build_feature_stream(events, window_size=window_size, step_size=step_size)
        sequences.append(feature_stream)
    return sequences


def build_takeover_session(
    rng: np.random.Generator,
    owner_profile: TypingProfile,
    impostor_profile: TypingProfile,
    events_per_part: int,
    window_size: int,
    step_size: int,
) -> Tuple[np.ndarray, int]:
    owner_events = generate_typing_events(events_per_part, owner_profile, rng)
    impostor_events = generate_typing_events(events_per_part, impostor_profile, rng)

    owner_stream = build_feature_stream(owner_events, window_size=window_size, step_size=step_size)
    impostor_stream = build_feature_stream(impostor_events, window_size=window_size, step_size=step_size)

    takeover_index = owner_stream.shape[0]
    session_stream = np.vstack([owner_stream, impostor_stream])
    return session_stream, takeover_index


def find_consecutive_low_scores(scores: np.ndarray, threshold: float, consecutive: int) -> int | None:
    count = 0
    for i, score in enumerate(scores):
        if score < threshold:
            count += 1
            if count >= consecutive:
                return i
        else:
            count = 0
    return None


def preview_scores(scores: np.ndarray, threshold: float, takeover_index: int) -> None:
    low = max(0, takeover_index - 8)
    high = min(len(scores), takeover_index + 12)
    for i in range(low, high):
        marker = "T" if i == takeover_index else " "
        label = "LOW" if scores[i] < threshold else "OK "
        print(f"{i:03d} {marker} score={scores[i]:.3f} {label}")


if __name__ == "__main__":
    main()
