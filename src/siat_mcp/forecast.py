"""Time-series forecasting for periods that are not in the data yet (annual, quarterly, monthly).

Small, transparent models only (the series are short): naive, drift, linear and log-linear trend, damped
Holt, and seasonal variants. The model is chosen per series by rolling-origin backtest, the interval comes
from the same backtest errors, and the forecast is refused when the series is too short, the target too far
or the past errors too large. A forecast is an estimate computed by code, never an official figure.
"""
from __future__ import annotations

import math

SEASON = {"year": 1, "quarter": 4, "month": 12}
MAX_HORIZON = {"year": 3, "quarter": 8, "month": 24}   # steps after the last published value
MIN_POINTS = {"year": 8, "quarter": 12, "month": 36}
INTERVAL_Q = 0.90        # backtest-error quantile; calibrated so the band covers ~80 % out of sample
MAX_ERROR = 0.20         # refuse when the best model's backtest error is above 20 %
MAX_HALF_WIDTH = 0.50    # refuse when the 80 % interval is wider than +-50 %

MODEL_NAMES = {
    "naive": "oxirgi qiymat saqlanadi",
    "drift": "o'rtacha qadamli siljish",
    "linear": "chiziqli trend",
    "loglinear": "o'rtacha o'sish sur'ati",
    "holt": "so'nuvchi trendli eksponensial silliqlash",
    "snaive": "o'tgan yilning shu davri",
    "sgrowth": "o'tgan yilning shu davri + yillik o'sish",
    "season_holt": "mavsumiylik + so'nuvchi trend",
    "season_linear": "mavsumiylik + chiziqli trend",
}


# --------------------------------------------------------------------------- base models: (train, h) -> [h values]
def _naive(y, h):
    return [y[-1]] * h


def _drift(y, h):
    w = y[-8:]
    step = (w[-1] - w[0]) / (len(w) - 1)
    return [y[-1] + step * i for i in range(1, h + 1)]


def _ols(w):
    n = len(w)
    mx, my = (n - 1) / 2, sum(w) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    b = sum((i - mx) * (v - my) for i, v in enumerate(w)) / sxx
    return my - b * mx, b


def _linear(y, h, window=8):
    w = y[-window:]
    a, b = _ols(w)
    return [a + b * (len(w) - 1 + i) for i in range(1, h + 1)]


def _loglinear(y, h, window=8):
    w = y[-window:]
    if min(w) <= 0:
        return None
    a, b = _ols([math.log(v) for v in w])
    return [math.exp(a + b * (len(w) - 1 + i)) for i in range(1, h + 1)]


def _holt(y, h):
    best = None
    for alpha in (0.3, 0.5, 0.8, 1.0):
        for beta in (0.1, 0.3, 0.6):
            for phi in (0.8, 0.9, 0.98):
                level, trend, sse = y[0], y[1] - y[0], 0.0
                for v in y[1:]:
                    pred = level + phi * trend
                    sse += (v - pred) ** 2
                    new = alpha * v + (1 - alpha) * pred
                    trend = beta * (new - level) + (1 - beta) * phi * trend
                    level = new
                if best is None or sse < best[0]:
                    best = (sse, level, trend, phi)
    _, level, trend, phi = best
    out, damp = [], 0.0
    for i in range(1, h + 1):
        damp += phi ** i
        out.append(level + damp * trend)
    return out


# --------------------------------------------------------------------------- seasonal models
def _seasonal_indices(y, m):
    """Per-position seasonal factor (multiplicative if the series is positive, else additive)."""
    mult = min(y) > 0
    half = m // 2
    ratios = [[] for _ in range(m)]
    for t in range(half, len(y) - half):
        cma = (0.5 * y[t - half] + sum(y[t - half + 1:t + half]) + 0.5 * y[t + half]) / m
        ratios[t % m].append(y[t] / cma if mult else y[t] - cma)
    idx = []
    for r in ratios:
        r = sorted(r)
        idx.append((r[len(r) // 2] if len(r) % 2 else (r[len(r) // 2 - 1] + r[len(r) // 2]) / 2) if r else (1.0 if mult else 0.0))
    mean = sum(idx) / m
    idx = [v / mean for v in idx] if mult else [v - mean for v in idx]
    return idx, mult


def _season_wrap(base):
    def model(y, h, m):
        if len(y) < 2 * m + 2:
            return None
        idx, mult = _seasonal_indices(y, m)
        adj = [v / idx[t % m] if mult else v - idx[t % m] for t, v in enumerate(y)]
        out = base(adj, h) if base is _holt else _linear(adj, h, window=3 * m)
        n = len(y)
        return [v * idx[(n + i) % m] if mult else v + idx[(n + i) % m] for i, v in enumerate(out)]
    return model


def _snaive(y, h, m):
    if len(y) < m:
        return None
    return [y[len(y) - m + (i % m)] for i in range(h)]


def _sgrowth(y, h, m):
    if len(y) < 2 * m or min(y[-2 * m:]) <= 0:
        return None
    g = min(1.5, max(0.7, sum(y[-m:]) / sum(y[-2 * m:-m])))
    return [y[len(y) - m + (i % m)] * g ** (i // m + 1) for i in range(h)]


def _models(m):
    if m == 1:
        return {"naive": _naive, "drift": _drift, "linear": _linear, "loglinear": _loglinear, "holt": _holt}
    return {
        "naive": _naive,
        "holt": _holt,
        "snaive": lambda y, h: _snaive(y, h, m),
        "sgrowth": lambda y, h: _sgrowth(y, h, m),
        "season_holt": lambda y, h: _season_wrap(_holt)(y, h, m),
        "season_linear": lambda y, h: _season_wrap(_linear)(y, h, m),
    }


# --------------------------------------------------------------------------- selection
def _quantile(xs, q):
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _backtest(y, h, m, model):
    """Rolling origin: -> (errors by step [(abs_error, actual, predicted)], weighted abs % error) or None."""
    min_train = 6 if m == 1 else 2 * m + 2
    n_origins = min(8 if m == 1 else m + 4, len(y) - min_train)
    if n_origins < 2:
        return None
    errs: dict[int, list] = {}
    for o in range(len(y) - n_origins, len(y)):
        pred = model(y[:o], min(h, len(y) - o))
        if pred is None:
            return None
        for s, p in enumerate(pred, 1):
            a = y[o + s - 1]
            errs.setdefault(s, []).append((abs(a - p), a, p))
    flat = [e for v in errs.values() for e in v]
    denom = sum(abs(a) for _, a, _ in flat)
    if denom == 0:
        return None
    return errs, sum(e for e, _, _ in flat) / denom


def forecast(values: list[float], horizon: int, season: int) -> dict:
    """values: contiguous history. -> {ok, model, error, points:[(value, low, high)], backtest_points}
    or {ok: False, reason}."""
    scored = []
    for name, model in _models(season).items():
        bt = _backtest(values, horizon, season, model)
        if bt is None:
            continue
        pred = model(values, horizon)
        if pred is None or not all(math.isfinite(p) for p in pred):
            continue
        scored.append((bt[1], name, bt[0], pred))
    if not scored:
        return {"ok": False, "reason": "too_short"}
    error, name, errs, pred = min(scored, key=lambda x: x[0])
    if error > MAX_ERROR:
        return {"ok": False, "reason": "unreliable", "error": error}

    rel = {s: [e / max(abs(p), 1e-9) for e, _, p in v] for s, v in errs.items()}
    pooled = [x for v in rel.values() for x in v]
    avg_step = sum(s * len(v) for s, v in rel.items()) / len(pooled)
    base, width, points = _quantile(pooled, INTERVAL_Q), 0.0, []
    nonneg = min(values) >= 0
    for s, p in enumerate(pred, 1):
        q = base * math.sqrt(s / avg_step)
        if len(rel.get(s, [])) >= 4:
            q = max(q, _quantile(rel[s], INTERVAL_Q))
        width = max(width, q)  # uncertainty never shrinks further into the future
        if nonneg:
            p = max(p, 0.0)
        low, high = p - width * abs(p), p + width * abs(p)
        points.append((p, max(low, 0.0) if nonneg else low, high))
    if width > MAX_HALF_WIDTH:
        return {"ok": False, "reason": "unreliable", "error": error}
    return {"ok": True, "model": name, "model_uz": MODEL_NAMES[name], "error": error, "points": points,
            "backtest_points": len(pooled)}
