"""Metode GPU de analiză a DISTRIBUȚIEI / GEOMETRIEI numerelor pe biletul fizic.

Spre deosebire de metodele temporale (care tratează fiecare număr ca o serie),
acestea mapează numerele pe GRILA OFICIALĂ a biletului (cum apare la loto.ro) și
analizează tipare SPAȚIALE: zone calde 2D, echilibru rânduri/coloane, și o rețea
convoluțională care învață geometria extragerii următoare.

Grila oficială (din biletele reale):
    6/49 → 7 coloane × 7 rânduri      (n-1)//7, (n-1)%7
    5/40 → 7 coloane × 6 rânduri      (n-1)//7, (n-1)%7
    Joker 5/45 → 5 coloane × 9 rânduri (n-1)//5, (n-1)%5

Rulează pe GPU (torch.cuda) dacă e disponibil; altfel cad pe CPU (tot torch) ca
să producă scoruri și pe stații fără CUDA. Clasificate GPU în benchmark.

ATENȚIE onestitate: loteria e aleatoare. Aceste metode NU prezic — concurează în
benchmark ca oricare alta; dacă nu bat random la 4+, decizia nu le va alege.
"""
from __future__ import annotations

import logging
import math
from typing import Callable, Dict, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geometrie grilă
# ---------------------------------------------------------------------------
def _grid_cols(max_num: int) -> int:
    """Nr. coloane pe biletul oficial, după univers."""
    return {49: 7, 40: 7, 45: 5, 20: 5}.get(int(max_num), 7)


def _grid_shape(max_num: int) -> Tuple[int, int]:
    cols = _grid_cols(max_num)
    rows = int(math.ceil(max_num / cols))
    return rows, cols


def _normalize(scores: Dict[int, float], max_num: int) -> Dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


def _cuda_ok() -> bool:
    """CUDA prezent? Dacă NU, metodele GPU se SAR (return {}), fără fallback pe CPU."""
    import os
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "-1":
        return False
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def _device():
    import torch
    return torch.device("cuda")


def _occurrence_grids(draws_2d: np.ndarray, max_num: int):
    """(n_draws, rows, cols) tensor binar: 1 unde numărul a fost extras."""
    import torch
    rows, cols = _grid_shape(max_num)
    n = draws_2d.shape[0]
    g = np.zeros((n, rows, cols), dtype=np.float32)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                r, c = (vi - 1) // cols, (vi - 1) % cols
                g[i, r, c] = 1.0
    return torch.from_numpy(g)


def _cell_to_num(r: int, c: int, cols: int) -> int:
    return r * cols + c + 1


# ---------------------------------------------------------------------------
# 1) Spatial KDE — densitate 2D netezită (conv2d Gaussian pe GPU)
# ---------------------------------------------------------------------------
def score_geo_spatial_kde(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """Hartă 2D de apariții (cu decădere recentă), netezită cu kernel Gaussian
    prin conv2d. Scor = densitatea spațială în jurul fiecărei celule → zone calde.
    """
    try:
        import torch
        import torch.nn.functional as F
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[geo_kde] torch indisponibil: {exc}")
        return {}
    if not _cuda_ok():
        return {}  # fără GPU → sărim (fără fallback CPU)
    if draws_2d.shape[0] < 10:
        return {}
    rows, cols = _grid_shape(max_num)
    dev = _device()
    grids = _occurrence_grids(draws_2d, max_num).to(dev)  # (n, rows, cols)
    n = grids.shape[0]
    # Ponderi de recență: extragerile recente cântăresc mai mult (half-life ~ n/3).
    w = torch.exp(-torch.arange(n - 1, -1, -1, device=dev, dtype=torch.float32) / max(n / 3.0, 1.0))
    heat = (grids * w.view(-1, 1, 1)).sum(dim=0)  # (rows, cols)
    # Kernel Gaussian 3×3 separabil (smoothing spațial).
    k = torch.tensor([[1., 2., 1.], [2., 4., 2.], [1., 2., 1.]], device=dev)
    k = (k / k.sum()).view(1, 1, 3, 3)
    sm = F.conv2d(heat.view(1, 1, rows, cols), k, padding=1).view(rows, cols)
    sm = sm.detach().cpu().numpy()
    scores: Dict[int, float] = {}
    for r in range(rows):
        for c in range(cols):
            num = _cell_to_num(r, c, cols)
            if 1 <= num <= max_num:
                scores[num] = float(sm[r, c])
    return _normalize(scores, max_num)


# ---------------------------------------------------------------------------
# 2) Row/Col balance — propensiune marginală rând × coloană (tensor ops GPU)
# ---------------------------------------------------------------------------
def score_geo_rowcol(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """Frecvențe marginale (cu recență) pe RÂNDURI și COLOANE. Scor(n) =
    row_w[r(n)] × col_w[c(n)] — captează dezechilibrul distribuției geometrice.
    """
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[geo_rowcol] torch indisponibil: {exc}")
        return {}
    if not _cuda_ok():
        return {}  # fără GPU → sărim (fără fallback CPU)
    if draws_2d.shape[0] < 10:
        return {}
    rows, cols = _grid_shape(max_num)
    dev = _device()
    grids = _occurrence_grids(draws_2d, max_num).to(dev)
    n = grids.shape[0]
    w = torch.exp(-torch.arange(n - 1, -1, -1, device=dev, dtype=torch.float32) / max(n / 3.0, 1.0))
    heat = (grids * w.view(-1, 1, 1)).sum(dim=0)  # (rows, cols)
    row_w = heat.sum(dim=1)  # (rows,)
    col_w = heat.sum(dim=0)  # (cols,)
    row_w = row_w / (row_w.mean() + 1e-9)
    col_w = col_w / (col_w.mean() + 1e-9)
    prop = torch.outer(row_w, col_w).detach().cpu().numpy()  # (rows, cols)
    scores: Dict[int, float] = {}
    for r in range(rows):
        for c in range(cols):
            num = _cell_to_num(r, c, cols)
            if 1 <= num <= max_num:
                scores[num] = float(prop[r, c])
    return _normalize(scores, max_num)


# ---------------------------------------------------------------------------
# 3) CNN spatial — învață geometria extragerii următoare (antrenat pe GPU)
# ---------------------------------------------------------------------------
def score_geo_cnn_next(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """CNN mic: din stiva de grile recente (decăzute) prezice grila următoare.
    Output = probabilitate per celulă → scor per număr. Antrenare scurtă pe GPU.
    """
    try:
        import torch
        import torch.nn as nn
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[geo_cnn] torch indisponibil: {exc}")
        return {}
    if not _cuda_ok():
        return {}  # fără GPU → sărim (fără fallback CPU)
    W = 8  # fereastra de grile folosită ca input
    if draws_2d.shape[0] < W + 20:
        return {}
    try:
        torch.manual_seed(42)
        rows, cols = _grid_shape(max_num)
        dev = _device()
        grids = _occurrence_grids(draws_2d, max_num).to(dev)  # (n, rows, cols)
        n = grids.shape[0]
        # Input t = suma decăzută a ultimelor W grile (înainte de t); target = grila t.
        decay = torch.exp(-torch.arange(W - 1, -1, -1, device=dev, dtype=torch.float32) / 3.0)
        X, Y = [], []
        for t in range(W, n):
            win = grids[t - W:t]                       # (W, rows, cols)
            img = (win * decay.view(-1, 1, 1)).sum(0)  # (rows, cols)
            X.append(img)
            Y.append(grids[t])
        X = torch.stack(X).unsqueeze(1)  # (m, 1, rows, cols)
        Y = torch.stack(Y).unsqueeze(1)  # (m, 1, rows, cols)

        model = nn.Sequential(
            nn.Conv2d(1, 8, 3, padding=1), nn.ReLU(),
            nn.Conv2d(8, 8, 3, padding=1), nn.ReLU(),
            nn.Conv2d(8, 1, 3, padding=1),
        ).to(dev)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        lossf = nn.BCEWithLogitsLoss()
        model.train()
        for _ in range(60):
            opt.zero_grad()
            out = model(X)
            loss = lossf(out, Y)
            loss.backward()
            opt.step()
        # Predicție: forward pe ultima fereastră.
        model.eval()
        with torch.no_grad():
            last = (grids[n - W:] * decay.view(-1, 1, 1)).sum(0).view(1, 1, rows, cols)
            prob = torch.sigmoid(model(last)).view(rows, cols).cpu().numpy()
        scores: Dict[int, float] = {}
        for r in range(rows):
            for c in range(cols):
                num = _cell_to_num(r, c, cols)
                if 1 <= num <= max_num:
                    scores[num] = float(prob[r, c])
        return _normalize(scores, max_num)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[geo_cnn] eșec: {exc}")
        return {}


# ===========================================================================
# Registry
# ===========================================================================
GEOMETRY_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    "geo_spatial_kde_gpu": (score_geo_spatial_kde, "torch-geo-gpu", False,
                            "Densitate spațială 2D netezită (KDE conv pe grila biletului) — GPU"),
    "geo_rowcol_gpu":      (score_geo_rowcol,      "torch-geo-gpu", False,
                            "Propensiune geometrică rând × coloană pe bilet — GPU"),
    "geo_cnn_next_gpu":    (score_geo_cnn_next,    "torch-geo-gpu", True,
                            "CNN spațial: prezice geometria grilei următoare — GPU"),
}
