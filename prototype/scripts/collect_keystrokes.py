#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rc_auth import KeystrokeEvent, keystroke_event_to_dict

try:
    from pynput import keyboard
except ImportError:
    keyboard = None  # type: ignore[assignment]


@dataclass(frozen=True)
class SessionCapture:
    events: List[KeystrokeEvent]
    started_at: str
    finished_at: str


class LiveKeystrokeRecorder:
    def __init__(self, min_events: int) -> None:
        self.min_events = min_events
        self.events: List[KeystrokeEvent] = []
        self.pressed: Dict[str, Tuple[int, int, bool, bool]] = {}
        self.prev_release_ns: Optional[int] = None

        self.done_event = threading.Event()
        self.abort_requested = False
        self.lock = threading.Lock()

    def capture(self, timeout_sec: int) -> SessionCapture | None:
        started_at = datetime.now(timezone.utc).isoformat()
        with keyboard.Listener(on_press=self._on_press, on_release=self._on_release) as listener:
            done = self.done_event.wait(timeout=timeout_sec)
            listener.stop()

        if not done:
            raise TimeoutError(f"capture timeout: {timeout_sec}s")
        if self.abort_requested:
            return None

        finished_at = datetime.now(timezone.utc).isoformat()
        return SessionCapture(events=list(self.events), started_at=started_at, finished_at=finished_at)

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> bool | None:
        if key == keyboard.Key.esc:
            with self.lock:
                self.abort_requested = True
                self.done_event.set()
            return False

        key_code, is_backspace, accepted, is_enter = normalize_key(key)
        if not accepted:
            return None

        key_id = key_identity(key)
        now_ns = time.perf_counter_ns()

        with self.lock:
            if key_id in self.pressed:
                # Ignore repeat press while key is held down.
                return None
            self.pressed[key_id] = (now_ns, key_code, is_backspace, is_enter)
        return None

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode) -> bool | None:
        if key == keyboard.Key.esc:
            with self.lock:
                self.abort_requested = True
                self.done_event.set()
            return False

        key_id = key_identity(key)
        now_ns = time.perf_counter_ns()

        with self.lock:
            pressed_entry = self.pressed.pop(key_id, None)
            if pressed_entry is None:
                return None

            press_ns, key_code, is_backspace, is_enter = pressed_entry
            dwell_ms = max(0.0, (now_ns - press_ns) / 1_000_000.0)

            if self.prev_release_ns is None:
                flight_ms = 0.0
            else:
                flight_ms = max(0.0, (press_ns - self.prev_release_ns) / 1_000_000.0)

            self.prev_release_ns = now_ns
            self.events.append(
                KeystrokeEvent(
                    dwell_ms=float(dwell_ms),
                    flight_ms=float(flight_ms),
                    key_code=key_code,
                    is_backspace=is_backspace,
                )
            )

            if is_enter and len(self.events) >= self.min_events:
                self.done_event.set()
                return False

        return None


def key_identity(key: keyboard.Key | keyboard.KeyCode) -> str:
    if isinstance(key, keyboard.KeyCode):
        if key.char is not None:
            return f"char:{key.char}"
        if key.vk is not None:
            return f"vk:{key.vk}"
    return f"special:{key}"


def normalize_key(key: keyboard.Key | keyboard.KeyCode) -> Tuple[int, bool, bool, bool]:
    if key == keyboard.Key.backspace:
        return 8, True, True, False
    if key == keyboard.Key.enter:
        return 13, False, True, True
    if key == keyboard.Key.space:
        return 32, False, True, False

    if isinstance(key, keyboard.KeyCode) and key.char is not None:
        char = key.char
        if char.isprintable():
            return ord(char.lower()), False, True, False

    return 0, False, False, False


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect real keystroke sessions")
    parser.add_argument("--label", choices=("genuine", "impostor"), required=True)
    parser.add_argument("--sessions", type=int, default=6)
    parser.add_argument("--min-events", type=int, default=260)
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--countdown-sec", type=int, default=3)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "keystrokes",
        help="base output directory. files are saved to <output-dir>/<label>/",
    )
    args = parser.parse_args()

    if keyboard is None:
        raise SystemExit("pynput is not installed. Run: pip install -r requirements.txt")

    if args.sessions <= 0:
        raise SystemExit("--sessions must be > 0")
    if args.min_events <= 0:
        raise SystemExit("--min-events must be > 0")

    label_dir = args.output_dir / args.label
    label_dir.mkdir(parents=True, exist_ok=True)

    print("== Real Keystroke Collector ==")
    print(f"label={args.label} sessions={args.sessions} min_events={args.min_events}")
    print("Instruction:")
    print("- type naturally")
    print(f"- press Enter to finish each session (after at least {args.min_events} events)")
    print("- press ESC anytime to abort")

    for i in range(args.sessions):
        session_no = i + 1
        print(f"\n[{session_no}/{args.sessions}] Press Enter to start capture.")
        input()
        countdown(args.countdown_sec)

        recorder = LiveKeystrokeRecorder(min_events=args.min_events)
        try:
            capture = recorder.capture(timeout_sec=args.timeout_sec)
        except TimeoutError as exc:
            print(f"session timeout: {exc}")
            continue

        if capture is None:
            print("capture aborted by ESC")
            break

        if len(capture.events) < args.min_events:
            print(f"session ignored: only {len(capture.events)} events captured")
            continue

        out_path = save_capture(
            label_dir=label_dir,
            label=args.label,
            session_no=session_no,
            capture=capture,
            min_events=args.min_events,
        )
        print(f"saved: {out_path} ({len(capture.events)} events)")


def countdown(seconds: int) -> None:
    if seconds <= 0:
        return
    for remaining in range(seconds, 0, -1):
        print(f"start in {remaining}...", end="\r", flush=True)
        time.sleep(1)
    print(" " * 32, end="\r", flush=True)


def save_capture(
    label_dir: Path,
    label: str,
    session_no: int,
    capture: SessionCapture,
    min_events: int,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = label_dir / f"{label}_{timestamp}_s{session_no:02d}.json"

    payload = {
        "version": "1.0",
        "label": label,
        "sessionNo": session_no,
        "startedAt": capture.started_at,
        "finishedAt": capture.finished_at,
        "minEvents": min_events,
        "eventCount": len(capture.events),
        "events": [keystroke_event_to_dict(event) for event in capture.events],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


if __name__ == "__main__":
    main()
