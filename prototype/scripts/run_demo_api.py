#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, List, Literal, Sequence
from urllib.parse import parse_qs, urlparse

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rc_auth import (  # noqa: E402
    KeystrokeEvent,
    RCAuthConfig,
    RCContinuousAuthenticator,
    TypingProfile,
    build_feature_stream,
    generate_typing_events,
)

Mode = Literal["normal", "takeover"]


@dataclass(frozen=True)
class DatasetSplit:
    train_genuine: Sequence[np.ndarray]
    train_impostor: Sequence[np.ndarray]
    calib_genuine: Sequence[np.ndarray]
    calib_impostor: Sequence[np.ndarray]


@dataclass
class EnrolledState:
    model: RCContinuousAuthenticator
    threshold: float
    medium_threshold: float
    window_size: int
    step_size: int
    sample_count: int
    median_events: int
    created_at: str


class InferenceService:
    def __init__(
        self,
        seed: int,
        events_per_seq: int,
        num_train: int,
        num_calib: int,
        window_size: int,
        step_size: int,
    ) -> None:
        self.rng = np.random.default_rng(seed)
        self.events_per_seq = events_per_seq
        self.default_window_size = window_size
        self.default_step_size = step_size

        self.owner_profile = TypingProfile(
            dwell_mean=95.0,
            dwell_std=12.0,
            flight_mean=110.0,
            flight_std=16.0,
            backspace_prob=0.04,
            repeat_prob=0.02,
        )
        self.impostor_profile = TypingProfile(
            dwell_mean=145.0,
            dwell_std=24.0,
            flight_mean=185.0,
            flight_std=30.0,
            backspace_prob=0.18,
            repeat_prob=0.11,
        )

        split = self._build_dataset_split(
            owner_profile=self.owner_profile,
            impostor_profile=self.impostor_profile,
            events_per_seq=events_per_seq,
            num_train=num_train,
            num_calib=num_calib,
            window_size=window_size,
            step_size=step_size,
        )
        input_dim = split.train_genuine[0].shape[1]
        self.synthetic_model = RCContinuousAuthenticator(
            input_dim=input_dim,
            config=RCAuthConfig(
                reservoir_dim=256,
                sparsity=0.92,
                spectral_radius=0.9,
                leak_rate=0.25,
                ridge_lambda=1e-3,
                seed=seed,
            ),
        )
        self.synthetic_model.fit(split.train_genuine, split.train_impostor)
        self.synthetic_threshold = self.synthetic_model.calibrate_threshold(
            split.calib_genuine,
            split.calib_impostor,
        )

        self.enrolled: EnrolledState | None = None

    def get_enrollment_status(self) -> dict[str, Any]:
        if self.enrolled is None:
            return {
                "enrolled": False,
                "sampleCount": 0,
                "windowSize": self.default_window_size,
                "stepSize": self.default_step_size,
                "thresholds": None,
            }

        return {
            "enrolled": True,
            "sampleCount": self.enrolled.sample_count,
            "windowSize": self.enrolled.window_size,
            "stepSize": self.enrolled.step_size,
            "medianEvents": self.enrolled.median_events,
            "trainedAt": self.enrolled.created_at,
            "thresholds": {
                "high": self.enrolled.threshold,
                "medium": self.enrolled.medium_threshold,
            },
        }

    def reset_enrollment(self) -> dict[str, Any]:
        self.enrolled = None
        return self.get_enrollment_status()

    def enroll_user(
        self,
        samples_payload: Any,
        *,
        window_size: int | None = None,
        step_size: int | None = None,
    ) -> dict[str, Any]:
        ws = int(window_size) if window_size is not None else self.default_window_size
        ss = int(step_size) if step_size is not None else self.default_step_size
        if ws <= 0 or ss <= 0:
            raise ValueError("windowSize and stepSize must be > 0")

        if not isinstance(samples_payload, list):
            raise ValueError("samples must be an array")
        if len(samples_payload) < 4:
            raise ValueError("at least 4 enrollment samples are required")

        genuine_streams: List[np.ndarray] = []
        sample_events: List[List[KeystrokeEvent]] = []
        event_counts: List[int] = []

        for idx, raw_sample in enumerate(samples_payload):
            stream, events = self._feature_stream_from_raw_events(raw_sample, window_size=ws, step_size=ss)
            if stream.shape[0] < 2:
                raise ValueError(
                    f"sample[{idx}] produced too few windows ({stream.shape[0]}). type a longer text"
                )
            genuine_streams.append(stream)
            sample_events.append(events)
            event_counts.append(len(events))

        impostor_profile = self._derive_impostor_profile(sample_events)
        median_events = int(np.median(event_counts))
        impostor_streams = self._build_sequences(
            profile=impostor_profile,
            n_sequences=len(genuine_streams),
            events_per_seq=median_events,
            window_size=ws,
            step_size=ss,
        )

        split = self._split_for_training(genuine_streams, impostor_streams)
        model = RCContinuousAuthenticator(
            input_dim=genuine_streams[0].shape[1],
            config=RCAuthConfig(
                reservoir_dim=256,
                sparsity=0.92,
                spectral_radius=0.9,
                leak_rate=0.25,
                ridge_lambda=1e-3,
                seed=int(self.rng.integers(0, 1_000_000)),
            ),
        )
        model.fit(split.train_genuine, split.train_impostor)
        threshold = model.calibrate_threshold(split.calib_genuine, split.calib_impostor)
        medium_threshold = self._make_medium_threshold(threshold)

        genuine_avg = float(np.mean([np.mean(model.score_sequence(seq)) for seq in genuine_streams]))
        impostor_avg = float(np.mean([np.mean(model.score_sequence(seq)) for seq in impostor_streams]))

        self.enrolled = EnrolledState(
            model=model,
            threshold=float(threshold),
            medium_threshold=float(medium_threshold),
            window_size=ws,
            step_size=ss,
            sample_count=len(genuine_streams),
            median_events=median_events,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        return {
            "enrolled": True,
            "sampleCount": len(genuine_streams),
            "windowSize": ws,
            "stepSize": ss,
            "thresholds": {
                "high": float(threshold),
                "medium": float(medium_threshold),
            },
            "quality": {
                "genuineAvgScore": genuine_avg,
                "impostorAvgScore": impostor_avg,
                "separationGap": genuine_avg - impostor_avg,
            },
        }

    def authenticate_user(self, raw_events: Any) -> dict[str, Any]:
        if self.enrolled is None:
            raise RuntimeError("user is not enrolled yet")

        state = self.enrolled
        sequence, _events = self._feature_stream_from_raw_events(
            raw_events,
            window_size=state.window_size,
            step_size=state.step_size,
        )

        scores = state.model.score_sequence(sequence)
        avg_score = float(np.mean(scores))
        low_mask = scores < state.threshold
        low_ratio = float(np.mean(low_mask))
        low_count = int(np.sum(low_mask))
        trigger_index = find_consecutive_low_scores(scores, state.threshold, consecutive=3)

        risk_level = "LOW"
        if avg_score < state.threshold:
            risk_level = "HIGH"
        elif avg_score < state.medium_threshold:
            risk_level = "MEDIUM"

        accepted = avg_score >= state.medium_threshold and trigger_index is None
        if accepted:
            decision = "ALLOW"
        elif trigger_index is not None or avg_score < state.threshold:
            decision = "STEP-UP"
        else:
            decision = "MONITOR"

        points = [
            {
                "index": i,
                "score": float(score),
                "phase": "genuine",
            }
            for i, score in enumerate(scores)
        ]

        return {
            "accepted": accepted,
            "riskLevel": risk_level,
            "decision": decision,
            "thresholds": {
                "high": state.threshold,
                "medium": state.medium_threshold,
            },
            "summary": {
                "avgScore": avg_score,
                "windows": int(len(scores)),
                "lowWindows": low_count,
                "lowRatio": low_ratio,
                "triggerIndex": trigger_index,
            },
            "points": points,
        }

    def generate_synthetic_session(self, mode: Mode) -> dict[str, Any]:
        owner_a = generate_typing_events(self.events_per_seq, self.owner_profile, self.rng)
        owner_b = generate_typing_events(self.events_per_seq, self.owner_profile, self.rng)
        owner_stream = build_feature_stream(
            owner_a,
            window_size=self.default_window_size,
            step_size=self.default_step_size,
        )
        owner_stream_2 = build_feature_stream(
            owner_b,
            window_size=self.default_window_size,
            step_size=self.default_step_size,
        )

        takeover_index: int | None = None
        if mode == "normal":
            session_stream = np.vstack([owner_stream, owner_stream_2])
            phase_by_index = ["genuine"] * session_stream.shape[0]
        else:
            impostor_events = generate_typing_events(self.events_per_seq, self.impostor_profile, self.rng)
            impostor_stream = build_feature_stream(
                impostor_events,
                window_size=self.default_window_size,
                step_size=self.default_step_size,
            )
            takeover_index = owner_stream.shape[0]
            session_stream = np.vstack([owner_stream, impostor_stream])
            phase_by_index = ["genuine"] * takeover_index + ["takeover"] * impostor_stream.shape[0]

        scores = self.synthetic_model.score_sequence(session_stream)
        points = [
            {
                "index": i,
                "score": float(score),
                "phase": phase_by_index[i],
            }
            for i, score in enumerate(scores)
        ]

        return {
            "points": points,
            "takeoverIndex": takeover_index,
            "thresholds": {
                "high": float(self.synthetic_threshold),
                "medium": self._make_medium_threshold(self.synthetic_threshold),
            },
        }

    def _build_dataset_split(
        self,
        owner_profile: TypingProfile,
        impostor_profile: TypingProfile,
        events_per_seq: int,
        num_train: int,
        num_calib: int,
        window_size: int,
        step_size: int,
    ) -> DatasetSplit:
        n_total = num_train + num_calib
        genuine = self._build_sequences(
            profile=owner_profile,
            n_sequences=n_total,
            events_per_seq=events_per_seq,
            window_size=window_size,
            step_size=step_size,
        )
        impostor = self._build_sequences(
            profile=impostor_profile,
            n_sequences=n_total,
            events_per_seq=events_per_seq,
            window_size=window_size,
            step_size=step_size,
        )

        return DatasetSplit(
            train_genuine=genuine[:num_train],
            train_impostor=impostor[:num_train],
            calib_genuine=genuine[num_train : num_train + num_calib],
            calib_impostor=impostor[num_train : num_train + num_calib],
        )

    def _split_for_training(
        self,
        genuine_streams: Sequence[np.ndarray],
        impostor_streams: Sequence[np.ndarray],
    ) -> DatasetSplit:
        n = len(genuine_streams)
        if n != len(impostor_streams):
            raise ValueError("genuine and impostor sequence counts must match")
        if n < 4:
            raise ValueError("at least 4 sequences are required for training")

        num_calib = max(1, n // 3)
        num_train = n - num_calib
        if num_train < 2:
            raise ValueError("not enough sequences for training")

        return DatasetSplit(
            train_genuine=genuine_streams[:num_train],
            train_impostor=impostor_streams[:num_train],
            calib_genuine=genuine_streams[num_train:],
            calib_impostor=impostor_streams[num_train:],
        )

    def _build_sequences(
        self,
        profile: TypingProfile,
        n_sequences: int,
        events_per_seq: int,
        window_size: int,
        step_size: int,
    ) -> List[np.ndarray]:
        sequences: List[np.ndarray] = []
        for _ in range(n_sequences):
            events = generate_typing_events(events_per_seq, profile, self.rng)
            feature_stream = build_feature_stream(events, window_size=window_size, step_size=step_size)
            sequences.append(feature_stream)
        return sequences

    def _feature_stream_from_raw_events(
        self,
        raw_events: Any,
        *,
        window_size: int,
        step_size: int,
    ) -> tuple[np.ndarray, List[KeystrokeEvent]]:
        events = self._parse_events(raw_events)
        try:
            stream = build_feature_stream(events, window_size=window_size, step_size=step_size)
        except ValueError as exc:
            raise ValueError(
                f"invalid keystroke length: {exc} (events={len(events)}, windowSize={window_size})"
            ) from exc
        return stream, events

    def _parse_events(self, raw_events: Any) -> List[KeystrokeEvent]:
        if not isinstance(raw_events, list):
            raise ValueError("events must be an array")
        if not raw_events:
            raise ValueError("events must not be empty")

        events: List[KeystrokeEvent] = []
        for idx, item in enumerate(raw_events):
            if not isinstance(item, dict):
                raise ValueError(f"events[{idx}] must be an object")

            dwell_raw = item.get("dwellMs", item.get("dwell_ms"))
            flight_raw = item.get("flightMs", item.get("flight_ms"))
            key_code_raw = item.get("keyCode", item.get("key_code"))
            is_backspace_raw = item.get("isBackspace", item.get("is_backspace", False))

            try:
                dwell = float(dwell_raw)
                flight = float(flight_raw)
                key_code = int(key_code_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"events[{idx}] has invalid dwell/flight/keyCode") from exc

            if dwell <= 0 or flight <= 0:
                raise ValueError(f"events[{idx}] dwellMs and flightMs must be > 0")

            events.append(
                KeystrokeEvent(
                    dwell_ms=max(15.0, dwell),
                    flight_ms=max(10.0, flight),
                    key_code=key_code,
                    is_backspace=bool(is_backspace_raw),
                )
            )

        return events

    def _derive_impostor_profile(self, samples: Sequence[Sequence[KeystrokeEvent]]) -> TypingProfile:
        flat = [event for sample in samples for event in sample]
        if not flat:
            raise ValueError("samples must not be empty")

        dwell = np.array([e.dwell_ms for e in flat], dtype=np.float64)
        flight = np.array([e.flight_ms for e in flat], dtype=np.float64)
        keys = np.array([e.key_code for e in flat], dtype=np.int32)
        backspaces = np.array([1.0 if e.is_backspace else 0.0 for e in flat], dtype=np.float64)

        repeat_prob = float(np.mean(keys[1:] == keys[:-1])) if len(keys) >= 2 else 0.02
        backspace_prob = float(np.mean(backspaces))

        return TypingProfile(
            dwell_mean=float(np.mean(dwell) * 1.45 + 12.0),
            dwell_std=float(max(8.0, np.std(dwell) * 1.6)),
            flight_mean=float(np.mean(flight) * 1.5 + 15.0),
            flight_std=float(max(10.0, np.std(flight) * 1.6)),
            backspace_prob=float(min(0.5, backspace_prob + 0.12)),
            repeat_prob=float(min(0.35, repeat_prob + 0.08)),
        )

    def _make_medium_threshold(self, high_threshold: float) -> float:
        medium = float(min(0.98, high_threshold + 0.18))
        if medium <= high_threshold:
            medium = float(min(0.99, high_threshold + 0.05))
        return medium


class DemoRequestHandler(SimpleHTTPRequestHandler):
    service: InferenceService

    def __init__(self, *args, directory: str, **kwargs) -> None:
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/health":
            self._send_json({"status": "ok"})
            return

        if parsed.path == "/api/enroll/status":
            self._send_json(self.service.get_enrollment_status())
            return

        if parsed.path == "/api/session":
            query = parse_qs(parsed.query)
            mode = query.get("mode", ["normal"])[0]
            if mode not in ("normal", "takeover"):
                self._send_json({"error": "mode must be 'normal' or 'takeover'"}, status=HTTPStatus.BAD_REQUEST)
                return
            payload = self.service.generate_synthetic_session(mode=mode)
            self._send_json(payload)
            return

        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        try:
            if parsed.path == "/api/enroll":
                body = self._read_json_body()
                payload = self.service.enroll_user(
                    body.get("samples"),
                    window_size=body.get("windowSize"),
                    step_size=body.get("stepSize"),
                )
                self._send_json(payload)
                return

            if parsed.path == "/api/enroll/reset":
                payload = self.service.reset_enrollment()
                self._send_json(payload)
                return

            if parsed.path == "/api/authenticate":
                body = self._read_json_body()
                payload = self.service.authenticate_user(body.get("events"))
                self._send_json(payload)
                return

            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.CONFLICT)

    def _read_json_body(self) -> dict[str, Any]:
        content_len_raw = self.headers.get("Content-Length", "0")
        try:
            content_len = int(content_len_raw)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc

        if content_len <= 0:
            return {}

        raw = self.rfile.read(content_len)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("body must be valid JSON") from exc

        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve frontend + RC inference API for demo capture")
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--events-per-seq", type=int, default=320)
    parser.add_argument("--num-train", type=int, default=24)
    parser.add_argument("--num-calib", type=int, default=8)
    parser.add_argument("--window-size", type=int, default=24)
    parser.add_argument("--step-size", type=int, default=8)
    args = parser.parse_args()

    frontend_dir = ROOT / "frontend"
    if not frontend_dir.exists():
        raise FileNotFoundError(f"frontend directory not found: {frontend_dir}")

    service = InferenceService(
        seed=args.seed,
        events_per_seq=args.events_per_seq,
        num_train=args.num_train,
        num_calib=args.num_calib,
        window_size=args.window_size,
        step_size=args.step_size,
    )

    handler = lambda *handler_args, **handler_kwargs: DemoRequestHandler(
        *handler_args,
        directory=str(frontend_dir),
        **handler_kwargs,
    )
    DemoRequestHandler.service = service

    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}"
    print("RC demo API server is running.")
    print(f"- frontend:       {url}/")
    print(f"- health:         {url}/api/health")
    print(f"- enrollment:     POST {url}/api/enroll")
    print(f"- enrollmentStatus: GET  {url}/api/enroll/status")
    print(f"- authenticate:   POST {url}/api/authenticate")
    print(f"- synthetic demo: GET  {url}/api/session?mode=takeover")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
