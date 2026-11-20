"""L2 (streaming Half-Space Trees) and L3 (autoencoder) multivariate detectors per source.

Both look at a source's whole feature vector for one window, so they catch combinations
that are wrong together but fine alone. They corroborate L1 (layers_agreeing) rather than
raising alerts on their own, which keeps false alarms down.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

FEATURES = {
    "h": ["cpu", "mem", "disk", "net_util"],
    "link": ["rtt", "loss", "jitter", "up"],
    "svc": ["error_rate", "rtt", "up"],
}


def features_for(src: str) -> list[str]:
    return FEATURES.get(src.split(":", 1)[0], [])


@dataclass
class Scaler:
    lo: dict[str, float] = field(default_factory=dict)
    hi: dict[str, float] = field(default_factory=dict)

    def fit_one(self, x: dict[str, float]) -> None:
        for k, v in x.items():
            self.lo[k] = min(self.lo.get(k, v), v)
            self.hi[k] = max(self.hi.get(k, v), v)

    def transform(self, x: dict[str, float], keys: list[str]) -> dict[str, float]:
        out = {}
        for k in keys:
            lo, hi = self.lo.get(k, 0.0), self.hi.get(k, 1.0)
            span = (hi - lo) or 1.0
            out[k] = float(min(1.0, max(0.0, (x.get(k, lo) - lo) / span)))
        return out


class HST:
    """L2: river.anomaly.HalfSpaceTrees per source, calibrated to its own score history."""

    def __init__(self, warmup: int = 60):
        try:
            from river.anomaly import HalfSpaceTrees
        except Exception:  # pragma: no cover
            HalfSpaceTrees = None
        self._cls = HalfSpaceTrees
        self.models: dict[str, object] = {}
        self.scalers: dict[str, Scaler] = {}
        self.scores: dict[str, deque] = {}
        self.warmup = warmup

    def score(self, src: str, x: dict[str, float]) -> tuple[float | None, float | None]:
        if self._cls is None:
            return None, None
        keys = features_for(src)
        if not keys:
            return None, None
        m = self.models.setdefault(src, self._cls(n_trees=10, height=6, window_size=60, seed=42))
        sc = self.scalers.setdefault(src, Scaler())
        hist = self.scores.setdefault(src, deque(maxlen=500))
        xs = sc.transform(x, keys)
        s = float(m.score_one(xs))
        thr = float(np.percentile(hist, 99)) if len(hist) >= self.warmup else None
        if thr is None or s <= thr:
            sc.fit_one({k: x[k] for k in keys if k in x})
            m.learn_one(xs)
            hist.append(s)
        return round(s, 3), (round(thr, 3) if thr is not None else None)


class AutoEncoder:
    """L3: a small neural autoencoder (5->8->2->8->5 style, scikit-learn MLP) per source kind.

    Trained on warm-up vectors; anomaly when reconstruction error exceeds the 99th percentile
    of training error. Per-feature contribution = share of squared error.
    """

    def __init__(self, min_train: int = 60, refit_every: int = 180):
        self.data: dict[str, deque] = {}
        self.models: dict[str, tuple] = {}
        self.min_train = min_train
        self.refit_every = refit_every
        self.since_fit: dict[str, int] = {}

    def _fit(self, kind: str) -> None:
        from sklearn.neural_network import MLPRegressor
        from sklearn.preprocessing import StandardScaler

        X = np.array(self.data[kind])
        sc = StandardScaler().fit(X)
        Xs = sc.transform(X)
        width = X.shape[1]
        mlp = MLPRegressor(hidden_layer_sizes=(8, 2, 8) if width >= 4 else (6, 2, 6), activation="tanh",
                           max_iter=400, random_state=0).fit(Xs, Xs)
        err = ((mlp.predict(Xs) - Xs) ** 2).sum(axis=1)
        self.models[kind] = (sc, mlp, float(np.percentile(err, 99)))
        self.since_fit[kind] = 0

    def observe(self, src: str, x: dict[str, float]) -> dict | None:
        kind = src.split(":", 1)[0]
        keys = features_for(src)
        if not keys or any(k not in x for k in keys):
            return None
        vec = [x[k] for k in keys]
        result = None
        if kind in self.models:
            sc, mlp, thr = self.models[kind]
            xs = sc.transform([vec])
            sq = ((mlp.predict(xs) - xs) ** 2)[0]
            err = float(sq.sum())
            total = err or 1.0
            result = {"error": round(err, 3), "threshold": round(thr, 3), "anomalous": err > thr,
                      "contrib": {k: round(float(v) / total, 3) for k, v in zip(keys, sq)}}
            if result["anomalous"]:
                return result
        d = self.data.setdefault(kind, deque(maxlen=2000))
        d.append(vec)
        self.since_fit[kind] = self.since_fit.get(kind, 0) + 1
        if len(d) >= self.min_train and (kind not in self.models or self.since_fit[kind] >= self.refit_every):
            try:
                self._fit(kind)
            except Exception:
                pass
        return result
