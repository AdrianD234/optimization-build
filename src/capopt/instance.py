"""Instance loading: the benchmark-pack JSON turned into flat arrays that every solver component shares."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

PACK = Path(__file__).resolve().parents[2] / "benchmark_pack"
INSTANCE_NAMES = ["P100_raw_penalised", "P100_growth_penalised", "P100_raw_nopenalty", "P100_growth_nopenalty"]


@dataclass
class Instance:
    name: str
    T: int
    F: np.ndarray                 # funding per active year (int units)
    ids: list[str]
    spend: list[np.ndarray]       # per project, spend by build year (int units)
    nonzero: list[np.ndarray]     # per project, bool: build year counts as active
    last_nz: np.ndarray           # per project, index of the last active build year
    allowed: list[np.ndarray]     # per project, allowed start indices (sorted)
    pv: list[np.ndarray]          # per project, pv_int aligned with allowed
    backlog_weight: float
    pv_weight: float
    base_frac: float
    bands: list[tuple[float, float | None, float]]  # (from_frac, to_frac, weight)
    max_starts: int
    raw: dict[str, Any] = field(repr=False)

    @property
    def n(self) -> int:
        return len(self.ids)

    @property
    def penalised(self) -> bool:
        return self.backlog_weight > 0 or any(w > 0 for _, _, w in self.bands)

    @property
    def total_spend(self) -> int:
        return int(sum(int(s.sum()) for s in self.spend))

    def min_last_year(self) -> int:
        """Smallest possible last build year: cumulative funding must cover total spend (the balance is never
        negative) and every project must be able to finish."""
        cum = np.cumsum(self.F)
        idx = np.nonzero(cum >= self.total_spend)[0]
        by_funding = int(idx[0]) if len(idx) else self.T - 1
        return max(by_funding, max(int(a[0]) + int(z) for a, z in zip(self.allowed, self.last_nz)))

    def pv_of(self, p: int, s: int) -> int:
        j = int(np.searchsorted(self.allowed[p], s))
        if j >= len(self.allowed[p]) or self.allowed[p][j] != s:
            raise KeyError(f"{self.ids[p]}: start {s} not allowed")
        return int(self.pv[p][j])

    def starts_to_dict(self, starts: np.ndarray | list[int]) -> dict[str, int]:
        return {pid: int(s) for pid, s in zip(self.ids, starts)}

    def starts_from_dict(self, d: dict[str, int]) -> np.ndarray:
        return np.array([int(d[pid]) for pid in self.ids], dtype=np.int64)


def from_dict(raw: dict[str, Any]) -> Instance:
    P = raw["projects"]
    nonzero = [np.array(p["spend_nonzero"], dtype=bool) for p in P]
    return Instance(
        name=raw["name"],
        T=int(raw["T"]),
        F=np.array(raw["funding_S"], dtype=np.int64),
        ids=[p["id"] for p in P],
        spend=[np.array(p["spend_S"], dtype=np.int64) for p in P],
        nonzero=nonzero,
        last_nz=np.array([int(np.nonzero(z)[0][-1]) for z in nonzero], dtype=np.int64),
        allowed=[np.array(p["allowed_starts"], dtype=np.int64) for p in P],
        pv=[np.array(p["pv_int"], dtype=np.int64) for p in P],
        backlog_weight=float(raw["weights"]["backlog_weight"]),
        pv_weight=float(raw["weights"]["pv_weight"]),
        base_frac=float(raw["tiers"]["base_cap_frac"]),
        bands=[(float(b["from_frac"]), None if b["to_frac"] is None else float(b["to_frac"]), float(b["weight"]))
               for b in raw["tiers"]["bands"]],
        max_starts=int(raw["max_starts_per_year"]),
        raw=raw,
    )


def load(name_or_path: str | Path) -> Instance:
    path = Path(name_or_path)
    if not path.suffix:
        path = PACK / "instances" / f"{name_or_path}.json"
    return from_dict(json.loads(path.read_text(encoding="utf-8")))


def best_known(name: str) -> dict[str, Any]:
    return json.loads((PACK / "solutions" / f"{name}_best_known.json").read_text(encoding="utf-8"))
