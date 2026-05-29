"""Custom deep learning prediction methods (GPU-only, LUPTATORI environment).

These methods train small PyTorch models on per-number binary indicator
series and predict 1-step-ahead probability. They GRACEFULLY DEGRADE to
empty {} on CPU-only machines (CUDA_VISIBLE_DEVICES=-1 or no CUDA).

Total training time scales with max_num × n_models — on LUPTATORI with
RTX 5060 Ti, each method takes ~30-90s per game per fold. With 10 methods
× 10 folds × 3 games ≈ 4-8 hours additional benchmark time.
"""
from __future__ import annotations

import logging
import os
import warnings
from typing import Dict, Tuple, Callable, Optional

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


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


def _build_binary(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    n_draws = draws_2d.shape[0]
    bm = np.zeros((max_num, n_draws), dtype=np.float32)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                bm[vi - 1, i] = 1.0
    return bm


def _cuda_ok() -> bool:
    """Strict GPU check (LUPTATORI-only behavior)."""
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "-1":
        return False
    if os.environ.get("LOTO_SKIP_TIMESFM") == "1":
        return False
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Multi-series training: stack all max_num sequences into one batch tensor.
# Predicts P(next_step=1) per number in a single forward pass.
# ---------------------------------------------------------------------------

def _train_torch_model_batched(model_factory, draws_2d, max_num,
                                lag: int = 32, epochs: int = 30,
                                batch_size: int = 64, lr: float = 1e-3) -> Dict[int, float]:
    """Train one model SHARED across all numbers (multi-task) on lag windows.

    Each training example is (lag_window, target) for one number at one timestep.
    The model predicts P(target=1 | lag_window). Training data = all timesteps
    × all numbers (massive batch). Final prediction = each number's most recent
    lag_window forwarded through the trained model.
    """
    if not _cuda_ok():
        return {}
    if draws_2d.shape[0] < lag + 10:
        return {}
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
        device = torch.device("cuda")
        binary = _build_binary(draws_2d, max_num)
        n = binary.shape[1]
        # Build training set: for each number, for each valid timestep t > lag,
        # the input is binary[i, t-lag:t] and target is binary[i, t].
        X_list = []
        y_list = []
        for i in range(max_num):
            s = binary[i]
            for t in range(lag, n):
                X_list.append(s[t - lag:t])
                y_list.append(s[t])
        if not X_list:
            return {}
        X = torch.tensor(np.stack(X_list), dtype=torch.float32, device=device)
        y = torch.tensor(np.array(y_list), dtype=torch.float32, device=device)

        model = model_factory(lag).to(device)
        opt = torch.optim.Adam(model.parameters(), lr=lr)
        loss_fn = nn.BCEWithLogitsLoss()

        dataset = TensorDataset(X, y)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=False)

        model.train()
        for _ in range(epochs):
            for xb, yb in loader:
                opt.zero_grad()
                logits = model(xb).squeeze(-1)
                loss = loss_fn(logits, yb)
                loss.backward()
                opt.step()

        # Inference: most-recent lag-window per number
        model.eval()
        X_pred = torch.tensor(np.stack([binary[i, -lag:] for i in range(max_num)]),
                              dtype=torch.float32, device=device)
        with torch.no_grad():
            logits = model(X_pred).squeeze(-1)
            probs = torch.sigmoid(logits).cpu().numpy()
        scores = {i + 1: float(probs[i]) for i in range(max_num)}
        return _normalize(scores, max_num)
    except Exception as exc:
        logger.warning(f"[torch-extra] training failed: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Model factories (each returns a torch Module taking (B, lag) → (B, 1))
# ---------------------------------------------------------------------------

def _make_lstm(hidden: int = 32, layers: int = 1, bidir: bool = False):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class LSTMNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(1, hidden, num_layers=layers, batch_first=True, bidirectional=bidir)
                self.fc = nn.Linear(hidden * (2 if bidir else 1), 1)

            def forward(self, x):
                x = x.unsqueeze(-1)  # (B, lag, 1)
                out, _ = self.lstm(x)
                return self.fc(out[:, -1, :])
        return LSTMNet()
    return factory


def _make_gru(hidden: int = 32, layers: int = 1):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class GRUNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.gru = nn.GRU(1, hidden, num_layers=layers, batch_first=True)
                self.fc = nn.Linear(hidden, 1)

            def forward(self, x):
                x = x.unsqueeze(-1)
                out, _ = self.gru(x)
                return self.fc(out[:, -1, :])
        return GRUNet()
    return factory


def _make_cnn1d(channels: int = 16, kernel: int = 5):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class CNN1D(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv1 = nn.Conv1d(1, channels, kernel, padding=kernel // 2)
                self.conv2 = nn.Conv1d(channels, channels, kernel, padding=kernel // 2)
                self.fc = nn.Linear(channels * lag, 1)

            def forward(self, x):
                x = x.unsqueeze(1)  # (B, 1, lag)
                x = torch.relu(self.conv1(x))
                x = torch.relu(self.conv2(x))
                x = x.flatten(1)
                return self.fc(x)
        return CNN1D()
    return factory


def _make_tcn(channels: int = 16, kernel: int = 3, dilations=(1, 2, 4)):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class TCNBlock(nn.Module):
            def __init__(self, in_c, out_c, k, d):
                super().__init__()
                self.conv = nn.Conv1d(in_c, out_c, k, padding=(k - 1) * d, dilation=d)
                self.bn = nn.BatchNorm1d(out_c)

            def forward(self, x):
                return torch.relu(self.bn(self.conv(x)[..., :x.shape[-1]]))

        class TCN(nn.Module):
            def __init__(self):
                super().__init__()
                blocks = []
                in_c = 1
                for d in dilations:
                    blocks.append(TCNBlock(in_c, channels, kernel, d))
                    in_c = channels
                self.net = nn.Sequential(*blocks)
                self.fc = nn.Linear(channels * lag, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                x = self.net(x)
                return self.fc(x.flatten(1))
        return TCN()
    return factory


def _make_transformer_enc(d_model: int = 32, heads: int = 4, layers: int = 2, ff: int = 64):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class TransEnc(nn.Module):
            def __init__(self):
                super().__init__()
                self.embed = nn.Linear(1, d_model)
                self.pos = nn.Parameter(torch.zeros(1, lag, d_model))
                enc_layer = nn.TransformerEncoderLayer(d_model, heads, ff, batch_first=True)
                self.enc = nn.TransformerEncoder(enc_layer, num_layers=layers)
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                x = x.unsqueeze(-1)  # (B, lag, 1)
                x = self.embed(x) + self.pos
                x = self.enc(x)
                return self.fc(x[:, -1, :])
        return TransEnc()
    return factory


def _make_mlp(hidden=(64, 32)):
    def factory(lag: int):
        import torch
        import torch.nn as nn
        layers_l = []
        prev = lag
        for h in hidden:
            layers_l.append(nn.Linear(prev, h))
            layers_l.append(nn.ReLU())
            prev = h
        layers_l.append(nn.Linear(prev, 1))
        return nn.Sequential(*layers_l)
    return factory


def _make_resnet1d(channels: int = 16, blocks: int = 3):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class Residual(nn.Module):
            def __init__(self, c):
                super().__init__()
                self.conv1 = nn.Conv1d(c, c, 3, padding=1)
                self.conv2 = nn.Conv1d(c, c, 3, padding=1)
                self.bn1 = nn.BatchNorm1d(c)
                self.bn2 = nn.BatchNorm1d(c)

            def forward(self, x):
                r = x
                x = torch.relu(self.bn1(self.conv1(x)))
                x = self.bn2(self.conv2(x))
                return torch.relu(x + r)

        class ResNet1D(nn.Module):
            def __init__(self):
                super().__init__()
                self.stem = nn.Conv1d(1, channels, 3, padding=1)
                self.blocks = nn.Sequential(*[Residual(channels) for _ in range(blocks)])
                self.fc = nn.Linear(channels * lag, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                x = self.stem(x)
                x = self.blocks(x)
                return self.fc(x.flatten(1))
        return ResNet1D()
    return factory


def _make_wavenet_lite(channels: int = 16, kernel: int = 2, dilations=(1, 2, 4, 8)):
    """Causal dilated conv stack (WaveNet-style)."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class WN(nn.Module):
            def __init__(self):
                super().__init__()
                self.stem = nn.Conv1d(1, channels, 1)
                self.layers = nn.ModuleList()
                for d in dilations:
                    self.layers.append(nn.Conv1d(channels, channels, kernel, dilation=d, padding=(kernel - 1) * d))
                self.fc = nn.Linear(channels, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                x = self.stem(x)
                for layer in self.layers:
                    h = layer(x)[..., :x.shape[-1]]
                    x = torch.relu(x + h)
                return self.fc(x[..., -1])
        return WN()
    return factory


# ===========================================================================
# Scorer entrypoints
# ===========================================================================

def score_torch_lstm_small(draws_2d, max_num):
    return _train_torch_model_batched(_make_lstm(hidden=16, layers=1), draws_2d, max_num)


def score_torch_lstm_med(draws_2d, max_num):
    return _train_torch_model_batched(_make_lstm(hidden=32, layers=2), draws_2d, max_num)


def score_torch_bilstm(draws_2d, max_num):
    return _train_torch_model_batched(_make_lstm(hidden=32, layers=1, bidir=True), draws_2d, max_num)


def score_torch_gru_small(draws_2d, max_num):
    return _train_torch_model_batched(_make_gru(hidden=16, layers=1), draws_2d, max_num)


def score_torch_gru_med(draws_2d, max_num):
    return _train_torch_model_batched(_make_gru(hidden=32, layers=2), draws_2d, max_num)


def score_torch_cnn1d(draws_2d, max_num):
    return _train_torch_model_batched(_make_cnn1d(channels=16, kernel=5), draws_2d, max_num)


def score_torch_cnn1d_deep(draws_2d, max_num):
    return _train_torch_model_batched(_make_cnn1d(channels=32, kernel=3), draws_2d, max_num, epochs=40)


def score_torch_tcn_custom(draws_2d, max_num):
    return _train_torch_model_batched(_make_tcn(channels=24, kernel=3, dilations=(1, 2, 4, 8)), draws_2d, max_num)


def score_torch_transformer_enc(draws_2d, max_num):
    return _train_torch_model_batched(_make_transformer_enc(d_model=32, heads=4, layers=2), draws_2d, max_num,
                                       epochs=20, batch_size=128)


def score_torch_transformer_deep(draws_2d, max_num):
    return _train_torch_model_batched(_make_transformer_enc(d_model=64, heads=8, layers=4, ff=128),
                                       draws_2d, max_num, epochs=25, batch_size=128)


def score_torch_mlp(draws_2d, max_num):
    return _train_torch_model_batched(_make_mlp(hidden=(64, 32)), draws_2d, max_num)


def score_torch_mlp_deep(draws_2d, max_num):
    return _train_torch_model_batched(_make_mlp(hidden=(128, 64, 32, 16)), draws_2d, max_num, epochs=40)


def score_torch_resnet1d(draws_2d, max_num):
    return _train_torch_model_batched(_make_resnet1d(channels=16, blocks=3), draws_2d, max_num)


def score_torch_wavenet(draws_2d, max_num):
    return _train_torch_model_batched(_make_wavenet_lite(channels=16, dilations=(1, 2, 4, 8)),
                                       draws_2d, max_num, epochs=35)


def score_torch_lstm_attn(draws_2d, max_num):
    """LSTM with self-attention head."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class LSTMAttn(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(1, 32, batch_first=True)
                self.attn = nn.MultiheadAttention(32, num_heads=4, batch_first=True)
                self.fc = nn.Linear(32, 1)

            def forward(self, x):
                x = x.unsqueeze(-1)
                out, _ = self.lstm(x)
                attn_out, _ = self.attn(out, out, out)
                return self.fc(attn_out[:, -1, :])
        return LSTMAttn()
    return _train_torch_model_batched(factory, draws_2d, max_num, epochs=25)


# ===========================================================================
# Registry
# ===========================================================================

TORCH_EXTRA_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    # Recurrent
    "torch_lstm_s":      (score_torch_lstm_small,    "torch-rnn-gpu",        True, "Custom LSTM hidden=16 (GPU)"),
    "torch_lstm_m":      (score_torch_lstm_med,      "torch-rnn-gpu",        True, "Custom LSTM hidden=32 layers=2 (GPU)"),
    "torch_bilstm":      (score_torch_bilstm,        "torch-rnn-gpu",        True, "Bidirectional LSTM (GPU)"),
    "torch_gru_s":       (score_torch_gru_small,     "torch-rnn-gpu",        True, "Custom GRU hidden=16 (GPU)"),
    "torch_gru_m":       (score_torch_gru_med,       "torch-rnn-gpu",        True, "Custom GRU hidden=32 (GPU)"),
    "torch_lstm_attn":   (score_torch_lstm_attn,     "torch-rnn-gpu",        True, "LSTM + Multi-Head Attention (GPU)"),
    # Convolutional
    "torch_cnn1d":       (score_torch_cnn1d,         "torch-cnn-gpu",        True, "1D CNN 2-layer (GPU)"),
    "torch_cnn1d_deep":  (score_torch_cnn1d_deep,    "torch-cnn-gpu",        True, "Deeper 1D CNN ch=32 (GPU)"),
    "torch_tcn":         (score_torch_tcn_custom,    "torch-cnn-gpu",        True, "Custom TCN with dilations (GPU)"),
    "torch_wavenet":     (score_torch_wavenet,       "torch-cnn-gpu",        True, "WaveNet-lite causal dilated conv (GPU)"),
    "torch_resnet1d":    (score_torch_resnet1d,      "torch-cnn-gpu",        True, "1D ResNet 3 blocks (GPU)"),
    # Transformer
    "torch_transformer":      (score_torch_transformer_enc,   "torch-transformer-gpu", True, "Transformer encoder small (GPU)"),
    "torch_transformer_deep": (score_torch_transformer_deep,  "torch-transformer-gpu", True, "Transformer 4 layers d=64 (GPU)"),
    # MLP
    "torch_mlp":         (score_torch_mlp,           "torch-mlp-gpu",        True, "MLP (64,32) GPU"),
    "torch_mlp_deep":    (score_torch_mlp_deep,      "torch-mlp-gpu",        True, "Deep MLP (128,64,32,16) GPU"),
}
