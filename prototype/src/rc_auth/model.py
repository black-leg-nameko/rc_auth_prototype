from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .reservoir import ReservoirConfig, RidgeReadout, SparseReservoir


@dataclass(frozen=True)
class RCAuthConfig:
    reservoir_dim: int = 256
    sparsity: float = 0.92
    spectral_radius: float = 0.9
    leak_rate: float = 0.25
    ridge_lambda: float = 1e-3
    seed: int = 42


class RCContinuousAuthenticator:
    def __init__(self, input_dim: int, config: RCAuthConfig) -> None:
        res_cfg = ReservoirConfig(
            input_dim=input_dim,
            reservoir_dim=config.reservoir_dim,
            sparsity=config.sparsity,
            spectral_radius=config.spectral_radius,
            leak_rate=config.leak_rate,
            ridge_lambda=config.ridge_lambda,
            seed=config.seed,
        )
        self.reservoir = SparseReservoir(res_cfg)
        self.readout = RidgeReadout(ridge_lambda=config.ridge_lambda)
        self.threshold = 0.5
        self.feature_mean: Optional[np.ndarray] = None
        self.feature_std: Optional[np.ndarray] = None

    def fit(
        self,
        genuine_sequences: Sequence[np.ndarray],
        impostor_sequences: Sequence[np.ndarray],
    ) -> None:
        self._fit_standardization(genuine_sequences, impostor_sequences)
        x_train: List[np.ndarray] = []
        y_train: List[np.ndarray] = []

        for seq in genuine_sequences:
            x_train.append(self._embed(seq))
            y_train.append(np.ones(seq.shape[0], dtype=np.float64))
        for seq in impostor_sequences:
            x_train.append(self._embed(seq))
            y_train.append(np.zeros(seq.shape[0], dtype=np.float64))

        x_mat = np.vstack(x_train)
        y_vec = np.concatenate(y_train)
        self.readout.fit(x_mat, y_vec)

    def calibrate_threshold(
        self,
        genuine_sequences: Sequence[np.ndarray],
        impostor_sequences: Sequence[np.ndarray],
    ) -> float:
        best_threshold = 0.5
        best_error = float("inf")

        for threshold in np.linspace(0.2, 0.8, 121):
            frr = self._sequence_error_rate(genuine_sequences, threshold, accept_if_above=True)
            far = self._sequence_error_rate(impostor_sequences, threshold, accept_if_above=False)
            total_error = frr + far
            if total_error < best_error:
                best_error = total_error
                best_threshold = float(threshold)

        self.threshold = best_threshold
        return best_threshold

    def score_sequence(self, sequence: np.ndarray) -> np.ndarray:
        embedded = self._embed(sequence)
        return self.readout.predict_score(embedded)

    def predict_sequence(self, sequence: np.ndarray) -> int:
        scores = self.score_sequence(sequence)
        return 1 if float(np.mean(scores)) >= self.threshold else 0

    def evaluate(
        self,
        genuine_sequences: Sequence[np.ndarray],
        impostor_sequences: Sequence[np.ndarray],
    ) -> Tuple[float, float]:
        frr = self._sequence_error_rate(genuine_sequences, self.threshold, accept_if_above=True)
        far = self._sequence_error_rate(impostor_sequences, self.threshold, accept_if_above=False)
        return frr, far

    def _embed(self, sequence: np.ndarray) -> np.ndarray:
        if sequence.ndim != 2:
            raise ValueError("sequence must be 2D")
        normalized = self._normalize(sequence)
        states = self.reservoir.run(normalized, reset=True)
        bias = np.ones((sequence.shape[0], 1), dtype=np.float64)
        return np.hstack([states, normalized, bias])

    def _normalize(self, sequence: np.ndarray) -> np.ndarray:
        if self.feature_mean is None or self.feature_std is None:
            raise RuntimeError("model not fitted")
        return (sequence - self.feature_mean) / self.feature_std

    def _sequence_error_rate(
        self,
        sequences: Sequence[np.ndarray],
        threshold: float,
        accept_if_above: bool,
    ) -> float:
        if not sequences:
            raise ValueError("sequences must not be empty")

        errors = 0
        for seq in sequences:
            accepted = float(np.mean(self.score_sequence(seq))) >= threshold
            if accept_if_above and not accepted:
                errors += 1
            if not accept_if_above and accepted:
                errors += 1
        return errors / len(sequences)

    def _fit_standardization(
        self,
        genuine_sequences: Sequence[np.ndarray],
        impostor_sequences: Sequence[np.ndarray],
    ) -> None:
        stacked = np.vstack([*genuine_sequences, *impostor_sequences])
        mean = np.mean(stacked, axis=0)
        std = np.std(stacked, axis=0)
        std = np.where(std < 1e-6, 1.0, std)
        self.feature_mean = mean
        self.feature_std = std
