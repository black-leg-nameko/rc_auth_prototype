from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


@dataclass(frozen=True)
class ReservoirConfig:
    input_dim: int
    reservoir_dim: int = 256
    sparsity: float = 0.92
    spectral_radius: float = 0.9
    leak_rate: float = 0.25
    input_scale: float = 0.5
    ridge_lambda: float = 1e-3
    seed: int = 42


class SparseReservoir:
    def __init__(self, config: ReservoirConfig) -> None:
        self.config = config
        self.rng = np.random.default_rng(config.seed)
        self.state = np.zeros(config.reservoir_dim, dtype=np.float64)

        self.w_in = self.rng.uniform(
            low=-config.input_scale,
            high=config.input_scale,
            size=(config.reservoir_dim, config.input_dim),
        )

        dense = self.rng.normal(0.0, 1.0, size=(config.reservoir_dim, config.reservoir_dim))
        mask = self.rng.random(size=dense.shape) < (1.0 - config.sparsity)
        sparse = dense * mask

        radius = _safe_spectral_radius(sparse)
        if radius > 0:
            sparse *= config.spectral_radius / radius

        self.w_res = sparse
        self.bias = self.rng.normal(0.0, 0.05, size=(config.reservoir_dim,))

    def reset_state(self) -> None:
        self.state = np.zeros_like(self.state)

    def step(self, u_t: np.ndarray) -> np.ndarray:
        pre = self.w_res @ self.state + self.w_in @ u_t + self.bias
        candidate = np.tanh(pre)
        self.state = (1.0 - self.config.leak_rate) * self.state + self.config.leak_rate * candidate
        return self.state.copy()

    def run(self, inputs: np.ndarray, reset: bool = True) -> np.ndarray:
        if inputs.ndim != 2:
            raise ValueError("inputs must be 2D: [timesteps, input_dim]")
        if inputs.shape[1] != self.config.input_dim:
            raise ValueError("inputs second dimension does not match input_dim")

        if reset:
            self.reset_state()

        outputs = np.zeros((inputs.shape[0], self.config.reservoir_dim), dtype=np.float64)
        for i, u_t in enumerate(inputs):
            outputs[i] = self.step(u_t)
        return outputs


class RidgeReadout:
    def __init__(self, ridge_lambda: float = 1e-3) -> None:
        self.ridge_lambda = ridge_lambda
        self.weights: Optional[np.ndarray] = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> None:
        if x.ndim != 2:
            raise ValueError("x must be 2D")
        if y.ndim != 1:
            raise ValueError("y must be 1D")
        if x.shape[0] != y.shape[0]:
            raise ValueError("x and y sample counts must match")

        xtx = x.T @ x
        ridge = self.ridge_lambda * np.eye(xtx.shape[0], dtype=np.float64)
        self.weights = np.linalg.solve(xtx + ridge, x.T @ y)

    def predict_score(self, x: np.ndarray) -> np.ndarray:
        if self.weights is None:
            raise RuntimeError("model not fitted")
        logits = x @ self.weights
        return _sigmoid(logits)


def _safe_spectral_radius(matrix: np.ndarray) -> float:
    if not np.any(matrix):
        return 0.0
    eigvals = np.linalg.eigvals(matrix)
    return float(np.max(np.abs(eigvals)))

