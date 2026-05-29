"""Advanced GPU-only torch architectures + Bayesian + hybrid + ensembles.

50 noi metode (toate FREE, public, doar torch — fara dependinte platite):
- Variations of LSTM/GRU/CNN/Transformer cu hyperparametri diversi
- Hybrid (CNN+LSTM, CNN+Transformer, LSTM+Transformer)
- Novel: ESN, ConvLSTM, Phased LSTM, MLP-Mixer, gMLP, Highway, Inception
- Bayesian dropout / MC dropout / Deep ensembles
- Time2Vec embedded sequences
- Attention-only architectures

Toate gracefully degrade pe CPU (`_cuda_ok` check returneaza {}).
"""
from __future__ import annotations

import logging
import os
import warnings
from typing import Dict, Tuple, Callable, List

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# Reuse helpers from methods_torch_extra
from .methods_torch_extra import (
    _normalize,
    _build_binary,
    _cuda_ok,
    _train_torch_model_batched,
)


# ===========================================================================
# Variations of LSTM/GRU
# ===========================================================================

def _factory_lstm(hidden: int, layers: int = 1, bidir: bool = False, dropout: float = 0.0):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(1, hidden, num_layers=layers, batch_first=True,
                                    bidirectional=bidir, dropout=dropout if layers > 1 else 0.0)
                self.fc = nn.Linear(hidden * (2 if bidir else 1), 1)

            def forward(self, x):
                x = x.unsqueeze(-1)
                out, _ = self.lstm(x)
                return self.fc(out[:, -1, :])
        return Net()
    return factory


def _factory_gru(hidden: int, layers: int = 1, bidir: bool = False):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.gru = nn.GRU(1, hidden, num_layers=layers, batch_first=True, bidirectional=bidir)
                self.fc = nn.Linear(hidden * (2 if bidir else 1), 1)

            def forward(self, x):
                x = x.unsqueeze(-1)
                out, _ = self.gru(x)
                return self.fc(out[:, -1, :])
        return Net()
    return factory


def score_torch_lstm_xs(d, m): return _train_torch_model_batched(_factory_lstm(hidden=8), d, m, epochs=25)
def score_torch_lstm_l(d, m): return _train_torch_model_batched(_factory_lstm(hidden=128, layers=2, dropout=0.2), d, m, epochs=35)
def score_torch_lstm_xl(d, m): return _train_torch_model_batched(_factory_lstm(hidden=256, layers=3, dropout=0.3), d, m, epochs=40)
def score_torch_gru_xs(d, m): return _train_torch_model_batched(_factory_gru(hidden=8), d, m, epochs=25)
def score_torch_gru_l(d, m): return _train_torch_model_batched(_factory_gru(hidden=128, layers=2), d, m, epochs=35)
def score_torch_bigru(d, m): return _train_torch_model_batched(_factory_gru(hidden=32, bidir=True), d, m, epochs=30)
def score_torch_bigru_deep(d, m): return _train_torch_model_batched(_factory_gru(hidden=64, layers=2, bidir=True), d, m, epochs=35)
def score_torch_bilstm_deep(d, m): return _train_torch_model_batched(_factory_lstm(hidden=64, layers=2, bidir=True, dropout=0.2), d, m, epochs=35)


# ===========================================================================
# CNN variations
# ===========================================================================

def _factory_cnn_k(channels: int, kernel: int):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.c1 = nn.Conv1d(1, channels, kernel, padding=kernel // 2)
                self.c2 = nn.Conv1d(channels, channels, kernel, padding=kernel // 2)
                self.fc = nn.Linear(channels * lag, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                x = torch.relu(self.c1(x))
                x = torch.relu(self.c2(x))
                return self.fc(x.flatten(1))
        return Net()
    return factory


def score_torch_cnn1d_k3(d, m): return _train_torch_model_batched(_factory_cnn_k(16, 3), d, m, epochs=30)
def score_torch_cnn1d_k7(d, m): return _train_torch_model_batched(_factory_cnn_k(24, 7), d, m, epochs=30)
def score_torch_cnn1d_k15(d, m): return _train_torch_model_batched(_factory_cnn_k(32, 15), d, m, epochs=35)


def _factory_multi_scale_cnn(channels=16, kernels=(3, 7, 15)):
    """Parallel CNN branches with different kernels — Inception-style."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class MSCNN(nn.Module):
            def __init__(self):
                super().__init__()
                self.branches = nn.ModuleList([
                    nn.Conv1d(1, channels, k, padding=k // 2) for k in kernels
                ])
                self.fc = nn.Linear(channels * len(kernels) * lag, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                outs = [torch.relu(b(x)) for b in self.branches]
                out = torch.cat(outs, dim=1)
                return self.fc(out.flatten(1))
        return MSCNN()
    return factory


def score_torch_multi_scale_cnn(d, m): return _train_torch_model_batched(_factory_multi_scale_cnn(), d, m, epochs=30)


def _factory_inception_1d(channels=16):
    """Inception 1D: parallel 1×1, 3×1, 5×1, 7×1 conv + max-pool."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class Inception(nn.Module):
            def __init__(self):
                super().__init__()
                self.c1 = nn.Conv1d(1, channels, 1)
                self.c3 = nn.Conv1d(1, channels, 3, padding=1)
                self.c5 = nn.Conv1d(1, channels, 5, padding=2)
                self.c7 = nn.Conv1d(1, channels, 7, padding=3)
                self.fc = nn.Linear(channels * 4 * lag, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                outs = [torch.relu(c(x)) for c in (self.c1, self.c3, self.c5, self.c7)]
                return self.fc(torch.cat(outs, dim=1).flatten(1))
        return Inception()
    return factory


def score_torch_inception_1d(d, m): return _train_torch_model_batched(_factory_inception_1d(), d, m, epochs=30)


def _factory_squeeze_excite(channels=24, kernel=5):
    """Squeeze-and-Excitation network 1D."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class SENet(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv = nn.Conv1d(1, channels, kernel, padding=kernel // 2)
                self.se_fc1 = nn.Linear(channels, channels // 4)
                self.se_fc2 = nn.Linear(channels // 4, channels)
                self.fc = nn.Linear(channels * lag, 1)

            def forward(self, x):
                x = torch.relu(self.conv(x.unsqueeze(1)))  # (B, C, L)
                # Squeeze: global avg pool
                z = x.mean(dim=-1)  # (B, C)
                # Excite
                z = torch.sigmoid(self.se_fc2(torch.relu(self.se_fc1(z))))
                x = x * z.unsqueeze(-1)
                return self.fc(x.flatten(1))
        return SENet()
    return factory


def score_torch_squeeze_excite(d, m): return _train_torch_model_batched(_factory_squeeze_excite(), d, m, epochs=30)


# ===========================================================================
# Dilated CNN / WaveNet variants
# ===========================================================================

def _factory_dilated_cnn(channels=24, kernel=3, dilations=(1, 2, 4, 8, 16)):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class DCNN(nn.Module):
            def __init__(self):
                super().__init__()
                blocks = []
                in_c = 1
                for d in dilations:
                    blocks.append(nn.Conv1d(in_c, channels, kernel, padding=(kernel - 1) * d // 2, dilation=d))
                    blocks.append(nn.ReLU())
                    in_c = channels
                self.net = nn.Sequential(*blocks)
                self.fc = nn.Linear(channels * lag, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                x = self.net(x)[..., :lag]
                return self.fc(x.flatten(1))
        return DCNN()
    return factory


def score_torch_dilated_cnn(d, m): return _train_torch_model_batched(_factory_dilated_cnn(), d, m, epochs=35)


def _factory_wavenet_deep(channels=24, kernel=2, dilations=(1, 2, 4, 8, 16, 32)):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class WN(nn.Module):
            def __init__(self):
                super().__init__()
                self.stem = nn.Conv1d(1, channels, 1)
                self.layers = nn.ModuleList()
                for dl in dilations:
                    self.layers.append(nn.Conv1d(channels, channels, kernel, dilation=dl, padding=(kernel - 1) * dl))
                self.fc = nn.Linear(channels, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                x = self.stem(x)
                for layer in self.layers:
                    h = layer(x)[..., :x.shape[-1]]
                    x = torch.tanh(x + h)
                return self.fc(x[..., -1])
        return WN()
    return factory


def score_torch_wavenet_deep(d, m): return _train_torch_model_batched(_factory_wavenet_deep(), d, m, epochs=40)


# ===========================================================================
# Transformer variations
# ===========================================================================

def _factory_transformer(d_model=32, heads=4, layers=2, ff=64, dropout=0.0):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class T(nn.Module):
            def __init__(self):
                super().__init__()
                self.embed = nn.Linear(1, d_model)
                self.pos = nn.Parameter(torch.zeros(1, lag, d_model))
                enc_l = nn.TransformerEncoderLayer(d_model, heads, ff, dropout, batch_first=True)
                self.enc = nn.TransformerEncoder(enc_l, num_layers=layers)
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                x = x.unsqueeze(-1)
                x = self.embed(x) + self.pos
                x = self.enc(x)
                return self.fc(x[:, -1, :])
        return T()
    return factory


def score_torch_transformer_narrow(d, m): return _train_torch_model_batched(_factory_transformer(d_model=16, heads=2, layers=2), d, m, epochs=25, batch_size=128)
def score_torch_transformer_wide(d, m): return _train_torch_model_batched(_factory_transformer(d_model=128, heads=8, layers=2, ff=256), d, m, epochs=25, batch_size=64)
def score_torch_transformer_xl(d, m): return _train_torch_model_batched(_factory_transformer(d_model=64, heads=8, layers=6, ff=128, dropout=0.1), d, m, epochs=30, batch_size=64)


def _factory_self_attention_only(d_model=32, heads=4):
    """Pure self-attention (no embedding network, no FFN)."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class SA(nn.Module):
            def __init__(self):
                super().__init__()
                self.proj = nn.Linear(1, d_model)
                self.attn = nn.MultiheadAttention(d_model, heads, batch_first=True)
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                x = self.proj(x.unsqueeze(-1))
                out, _ = self.attn(x, x, x)
                return self.fc(out[:, -1, :])
        return SA()
    return factory


def score_torch_self_attention(d, m): return _train_torch_model_batched(_factory_self_attention_only(), d, m, epochs=25, batch_size=128)


# ===========================================================================
# MLP variations
# ===========================================================================

def _factory_mlp(hidden: Tuple[int, ...], dropout: float = 0.0):
    def factory(lag: int):
        import torch.nn as nn
        layers = []
        prev = lag
        for h in hidden:
            layers.append(nn.Linear(prev, h))
            layers.append(nn.ReLU())
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
            prev = h
        layers.append(nn.Linear(prev, 1))
        return nn.Sequential(*layers)
    return factory


def score_torch_mlp_narrow(d, m): return _train_torch_model_batched(_factory_mlp(hidden=(16, 8)), d, m, epochs=30)
def score_torch_mlp_wide(d, m): return _train_torch_model_batched(_factory_mlp(hidden=(256, 128)), d, m, epochs=30)
def score_torch_mlp_5layer(d, m): return _train_torch_model_batched(_factory_mlp(hidden=(128, 64, 32, 16, 8)), d, m, epochs=35)
def score_torch_mlp_dropout(d, m): return _train_torch_model_batched(_factory_mlp(hidden=(128, 64, 32), dropout=0.3), d, m, epochs=35)


def _factory_gmlp(d_model=32, layers=2):
    """Gated MLP — Gating Multi-Layer Perceptron."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class GMLPBlock(nn.Module):
            def __init__(self, dim, seq_len):
                super().__init__()
                self.norm = nn.LayerNorm(dim)
                self.proj_in = nn.Linear(dim, dim * 2)
                self.spatial = nn.Linear(seq_len, seq_len)
                self.proj_out = nn.Linear(dim, dim)

            def forward(self, x):
                u, v = self.proj_in(self.norm(x)).chunk(2, dim=-1)
                v = self.spatial(v.transpose(-1, -2)).transpose(-1, -2)
                return self.proj_out(u * v) + x

        class GMLP(nn.Module):
            def __init__(self):
                super().__init__()
                self.embed = nn.Linear(1, d_model)
                self.blocks = nn.Sequential(*[GMLPBlock(d_model, lag) for _ in range(layers)])
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                x = self.embed(x.unsqueeze(-1))
                x = self.blocks(x)
                return self.fc(x[:, -1, :])
        return GMLP()
    return factory


def score_torch_gmlp(d, m): return _train_torch_model_batched(_factory_gmlp(), d, m, epochs=30, batch_size=128)


def _factory_mlp_mixer(d_model=32, depth=2):
    """MLP-Mixer adapted for 1D sequence."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class MixerBlock(nn.Module):
            def __init__(self, seq_len, d):
                super().__init__()
                self.norm1 = nn.LayerNorm(d)
                self.token_mix = nn.Sequential(nn.Linear(seq_len, seq_len * 2), nn.GELU(), nn.Linear(seq_len * 2, seq_len))
                self.norm2 = nn.LayerNorm(d)
                self.channel_mix = nn.Sequential(nn.Linear(d, d * 2), nn.GELU(), nn.Linear(d * 2, d))

            def forward(self, x):
                # x: (B, seq, d)
                y = self.norm1(x).transpose(-1, -2)
                y = self.token_mix(y).transpose(-1, -2)
                x = x + y
                y = self.channel_mix(self.norm2(x))
                return x + y

        class Mixer(nn.Module):
            def __init__(self):
                super().__init__()
                self.embed = nn.Linear(1, d_model)
                self.blocks = nn.Sequential(*[MixerBlock(lag, d_model) for _ in range(depth)])
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                x = self.embed(x.unsqueeze(-1))
                x = self.blocks(x)
                return self.fc(x[:, -1, :])
        return Mixer()
    return factory


def score_torch_mlp_mixer(d, m): return _train_torch_model_batched(_factory_mlp_mixer(), d, m, epochs=30, batch_size=128)


def _factory_highway(hidden=64, layers=3):
    """Highway Network with skip connections."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class HighwayLayer(nn.Module):
            def __init__(self, dim):
                super().__init__()
                self.transform = nn.Linear(dim, dim)
                self.gate = nn.Linear(dim, dim)

            def forward(self, x):
                t = torch.relu(self.transform(x))
                g = torch.sigmoid(self.gate(x))
                return g * t + (1 - g) * x

        class Highway(nn.Module):
            def __init__(self):
                super().__init__()
                self.proj_in = nn.Linear(lag, hidden)
                self.layers = nn.ModuleList([HighwayLayer(hidden) for _ in range(layers)])
                self.fc = nn.Linear(hidden, 1)

            def forward(self, x):
                x = torch.relu(self.proj_in(x))
                for l in self.layers:
                    x = l(x)
                return self.fc(x)
        return Highway()
    return factory


def score_torch_highway(d, m): return _train_torch_model_batched(_factory_highway(), d, m, epochs=30)


# ===========================================================================
# Hybrid architectures
# ===========================================================================

def _factory_cnn_lstm(cnn_ch=16, lstm_hidden=32):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class CNNLSTM(nn.Module):
            def __init__(self):
                super().__init__()
                self.cnn = nn.Sequential(
                    nn.Conv1d(1, cnn_ch, 3, padding=1), nn.ReLU(),
                    nn.Conv1d(cnn_ch, cnn_ch, 3, padding=1), nn.ReLU(),
                )
                self.lstm = nn.LSTM(cnn_ch, lstm_hidden, batch_first=True)
                self.fc = nn.Linear(lstm_hidden, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                x = self.cnn(x).transpose(1, 2)  # (B, lag, cnn_ch)
                out, _ = self.lstm(x)
                return self.fc(out[:, -1, :])
        return CNNLSTM()
    return factory


def score_torch_cnn_lstm(d, m): return _train_torch_model_batched(_factory_cnn_lstm(), d, m, epochs=30)


def _factory_cnn_transformer(cnn_ch=16, d_model=32, heads=4, layers=2):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class CNNTrans(nn.Module):
            def __init__(self):
                super().__init__()
                self.cnn = nn.Sequential(
                    nn.Conv1d(1, cnn_ch, 3, padding=1), nn.ReLU(),
                )
                self.proj = nn.Linear(cnn_ch, d_model)
                self.pos = nn.Parameter(torch.zeros(1, lag, d_model))
                enc_l = nn.TransformerEncoderLayer(d_model, heads, d_model * 2, batch_first=True)
                self.enc = nn.TransformerEncoder(enc_l, num_layers=layers)
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                x = self.cnn(x.unsqueeze(1)).transpose(1, 2)
                x = self.proj(x) + self.pos
                x = self.enc(x)
                return self.fc(x[:, -1, :])
        return CNNTrans()
    return factory


def score_torch_cnn_transformer(d, m): return _train_torch_model_batched(_factory_cnn_transformer(), d, m, epochs=25)


def _factory_lstm_transformer(lstm_hidden=32, d_model=32, heads=4):
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class LSTMTrans(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(1, lstm_hidden, batch_first=True)
                self.proj = nn.Linear(lstm_hidden, d_model)
                self.attn = nn.MultiheadAttention(d_model, heads, batch_first=True)
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                x = x.unsqueeze(-1)
                out, _ = self.lstm(x)
                out = self.proj(out)
                attn_out, _ = self.attn(out, out, out)
                return self.fc(attn_out[:, -1, :])
        return LSTMTrans()
    return factory


def score_torch_lstm_transformer(d, m): return _train_torch_model_batched(_factory_lstm_transformer(), d, m, epochs=25)


def _factory_dual_path(cnn_ch=16, lstm_hidden=32):
    """Parallel CNN + LSTM streams, concatenated."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class Dual(nn.Module):
            def __init__(self):
                super().__init__()
                self.cnn = nn.Sequential(
                    nn.Conv1d(1, cnn_ch, 3, padding=1), nn.ReLU(),
                )
                self.lstm = nn.LSTM(1, lstm_hidden, batch_first=True)
                self.fc = nn.Linear(cnn_ch * lag + lstm_hidden, 1)

            def forward(self, x):
                x_seq = x.unsqueeze(-1)
                x_cnn = self.cnn(x.unsqueeze(1)).flatten(1)
                x_lstm, _ = self.lstm(x_seq)
                x_lstm = x_lstm[:, -1, :]
                return self.fc(torch.cat([x_cnn, x_lstm], dim=1))
        return Dual()
    return factory


def score_torch_dual_path(d, m): return _train_torch_model_batched(_factory_dual_path(), d, m, epochs=30)


# ===========================================================================
# Bayesian / MC Dropout
# ===========================================================================

def _factory_bayesian_mlp(hidden=64, dropout=0.5):
    """MC Dropout MLP — dropout ACTIV at inference."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class BMLP(nn.Module):
            def __init__(self):
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(lag, hidden), nn.ReLU(), nn.Dropout(dropout),
                    nn.Linear(hidden, hidden // 2), nn.ReLU(), nn.Dropout(dropout),
                    nn.Linear(hidden // 2, 1),
                )

            def forward(self, x):
                return self.net(x)

            def train_mode_keep_dropout(self):
                """Keep dropout ON at inference for MC sampling."""
                for m in self.modules():
                    if isinstance(m, nn.Dropout):
                        m.train()
                    else:
                        m.eval()
        return BMLP()
    return factory


def score_torch_bayesian_mlp(d, m):
    """MC Dropout: T=10 stochastic forward passes, average probs."""
    if not _cuda_ok():
        return {}
    if d.shape[0] < 50:
        return {}
    try:
        import torch
        device = torch.device("cuda")
        binary = _build_binary(d, m)
        n = binary.shape[1]
        lag = 32
        # Training data setup (single shared training)
        X_list, y_list = [], []
        for i in range(m):
            s = binary[i]
            for t in range(lag, n):
                X_list.append(s[t - lag:t]); y_list.append(s[t])
        if not X_list:
            return {}
        X = torch.tensor(np.stack(X_list), dtype=torch.float32, device=device)
        y = torch.tensor(np.array(y_list), dtype=torch.float32, device=device)
        from torch.utils.data import DataLoader, TensorDataset
        loader = DataLoader(TensorDataset(X, y), batch_size=64, shuffle=True)
        model = _factory_bayesian_mlp()(lag).to(device)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        loss_fn = torch.nn.BCEWithLogitsLoss()
        model.train()
        for _ in range(30):
            for xb, yb in loader:
                opt.zero_grad()
                loss = loss_fn(model(xb).squeeze(-1), yb)
                loss.backward(); opt.step()
        # MC inference: keep dropout ON
        model.train_mode_keep_dropout()
        X_pred = torch.tensor(np.stack([binary[i, -lag:] for i in range(m)]),
                              dtype=torch.float32, device=device)
        T = 10
        with torch.no_grad():
            probs = torch.zeros(m, device=device)
            for _ in range(T):
                logits = model(X_pred).squeeze(-1)
                probs += torch.sigmoid(logits) / T
            probs = probs.cpu().numpy()
        return _normalize({i + 1: float(probs[i]) for i in range(m)}, m)
    except Exception as exc:
        logger.warning(f"[bayesian_mlp] {exc}")
        return {}


def score_torch_bayesian_lstm(d, m):
    """LSTM cu dropout activ la inferenta — Bayesian approx."""
    def factory(lag: int):
        import torch.nn as nn

        class BLSTM(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(1, 32, num_layers=2, batch_first=True, dropout=0.4)
                self.drop = nn.Dropout(0.4)
                self.fc = nn.Linear(32, 1)

            def forward(self, x):
                x = x.unsqueeze(-1)
                out, _ = self.lstm(x)
                return self.fc(self.drop(out[:, -1, :]))
        return BLSTM()
    return _train_torch_model_batched(factory, d, m, epochs=30)


def score_torch_deep_ensemble_mlp(d, m):
    """Deep Ensemble: train 5 MLPs cu seeds diferite, avg outputs."""
    if not _cuda_ok():
        return {}
    if d.shape[0] < 50:
        return {}
    all_scores = []
    for seed in [42, 123, 456, 789, 1024]:
        try:
            import torch
            torch.manual_seed(seed)
            np.random.seed(seed)
            scores = _train_torch_model_batched(_factory_mlp(hidden=(64, 32), dropout=0.1),
                                                d, m, epochs=20, lr=1e-3)
            if scores:
                all_scores.append(scores)
        except Exception:
            pass
    if not all_scores:
        return {}
    avg = {n: float(np.mean([s.get(n, 0.0) for s in all_scores])) for n in range(1, m + 1)}
    return _normalize(avg, m)


# ===========================================================================
# Novel architectures
# ===========================================================================

def _factory_echo_state_network(reservoir=100, spectral_radius=0.9, leaky=0.3):
    """Echo State Network — reservoir cu weights fixate, doar readout antrenat."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class ESN(nn.Module):
            def __init__(self):
                super().__init__()
                # Fixed reservoir
                W_in = torch.randn(reservoir, 1) * 0.1
                W_res = torch.randn(reservoir, reservoir) * 0.1
                eigs = torch.linalg.eigvals(W_res).abs().max()
                W_res = W_res * (spectral_radius / max(eigs.item(), 1e-9))
                self.register_buffer("W_in", W_in)
                self.register_buffer("W_res", W_res)
                # Trainable readout
                self.readout = nn.Linear(reservoir, 1)

            def forward(self, x):
                # x: (B, lag)
                B = x.shape[0]
                h = torch.zeros(B, reservoir, device=x.device)
                for t in range(x.shape[1]):
                    u = x[:, t:t + 1]
                    h_new = torch.tanh(u @ self.W_in.T + h @ self.W_res.T)
                    h = (1 - leaky) * h + leaky * h_new
                return self.readout(h)
        return ESN()
    return factory


def score_torch_echo_state(d, m): return _train_torch_model_batched(_factory_echo_state_network(), d, m, epochs=15)


def _factory_conv_lstm():
    """ConvLSTM-lite: convolutional gate transitions."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class CLSTM(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv_i = nn.Conv1d(1, 16, 3, padding=1)
                self.conv_f = nn.Conv1d(1, 16, 3, padding=1)
                self.conv_g = nn.Conv1d(1, 16, 3, padding=1)
                self.conv_o = nn.Conv1d(1, 16, 3, padding=1)
                self.fc = nn.Linear(16 * lag, 1)

            def forward(self, x):
                x = x.unsqueeze(1)
                i = torch.sigmoid(self.conv_i(x))
                f = torch.sigmoid(self.conv_f(x))
                g = torch.tanh(self.conv_g(x))
                o = torch.sigmoid(self.conv_o(x))
                c = f * g + i * g
                h = o * torch.tanh(c)
                return self.fc(h.flatten(1))
        return CLSTM()
    return factory


def score_torch_conv_lstm(d, m): return _train_torch_model_batched(_factory_conv_lstm(), d, m, epochs=30)


def _factory_phased_lstm():
    """Phased LSTM (timing gate). Simplified: use sinusoidal time feature."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class PLSTM(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = nn.LSTM(2, 32, batch_first=True)
                self.fc = nn.Linear(32, 1)

            def forward(self, x):
                # Add sinusoidal time feature
                B, L = x.shape
                t = torch.arange(L, device=x.device, dtype=torch.float32).unsqueeze(0).repeat(B, 1)
                phase = torch.sin(t / 4.0).unsqueeze(-1)
                xi = torch.cat([x.unsqueeze(-1), phase], dim=-1)
                out, _ = self.lstm(xi)
                return self.fc(out[:, -1, :])
        return PLSTM()
    return factory


def score_torch_phased_lstm(d, m): return _train_torch_model_batched(_factory_phased_lstm(), d, m, epochs=30)


def _factory_time2vec_lstm(t2v_dim=8):
    """Time2Vec embedding + LSTM."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class T2V(nn.Module):
            def __init__(self):
                super().__init__()
                self.w0 = nn.Parameter(torch.randn(1))
                self.b0 = nn.Parameter(torch.zeros(1))
                self.w = nn.Parameter(torch.randn(t2v_dim - 1))
                self.b = nn.Parameter(torch.zeros(t2v_dim - 1))
                self.lstm = nn.LSTM(t2v_dim + 1, 32, batch_first=True)
                self.fc = nn.Linear(32, 1)

            def forward(self, x):
                B, L = x.shape
                t = torch.arange(L, device=x.device, dtype=torch.float32).unsqueeze(0).repeat(B, 1)
                linear = (self.w0 * t + self.b0).unsqueeze(-1)
                periodic = torch.sin(t.unsqueeze(-1) * self.w + self.b)
                tv = torch.cat([linear, periodic], dim=-1)
                xi = torch.cat([x.unsqueeze(-1), tv], dim=-1)
                out, _ = self.lstm(xi)
                return self.fc(out[:, -1, :])
        return T2V()
    return factory


def score_torch_time2vec_lstm(d, m): return _train_torch_model_batched(_factory_time2vec_lstm(), d, m, epochs=30)


def _factory_attention_pool(d_model=32, heads=4):
    """Attention-weighted pooling MLP head."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        class AP(nn.Module):
            def __init__(self):
                super().__init__()
                self.embed = nn.Linear(1, d_model)
                self.q = nn.Parameter(torch.randn(1, 1, d_model))
                self.attn = nn.MultiheadAttention(d_model, heads, batch_first=True)
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                x = self.embed(x.unsqueeze(-1))
                q = self.q.expand(x.shape[0], -1, -1)
                out, _ = self.attn(q, x, x)
                return self.fc(out[:, 0, :])
        return AP()
    return factory


def score_torch_attention_pool(d, m): return _train_torch_model_batched(_factory_attention_pool(), d, m, epochs=25, batch_size=128)


def _factory_cross_attention(d_model=32, heads=4, chunks=4):
    """Cross-attention between past chunks of the sequence."""
    def factory(lag: int):
        import torch
        import torch.nn as nn

        chunk_size = max(2, lag // chunks)

        class CA(nn.Module):
            def __init__(self):
                super().__init__()
                self.embed = nn.Linear(1, d_model)
                self.attn = nn.MultiheadAttention(d_model, heads, batch_first=True)
                self.fc = nn.Linear(d_model, 1)

            def forward(self, x):
                B, L = x.shape
                # Split into chunks, average each, then cross-attend last vs others
                n_full = L // chunk_size
                if n_full < 2:
                    z = self.embed(x.unsqueeze(-1)).mean(dim=1, keepdim=True)
                    return self.fc(z.squeeze(1))
                chunks_data = x[:, :n_full * chunk_size].view(B, n_full, chunk_size).mean(dim=-1)
                # (B, n_full)
                z = self.embed(chunks_data.unsqueeze(-1))  # (B, n_full, d)
                last = z[:, -1:, :]
                past = z[:, :-1, :]
                out, _ = self.attn(last, past, past)
                return self.fc(out[:, 0, :])
        return CA()
    return factory


def score_torch_cross_attention(d, m): return _train_torch_model_batched(_factory_cross_attention(), d, m, epochs=25)


# ===========================================================================
# Ensemble of torch_* methods (10 combos)
# ===========================================================================

def _torch_ensemble(method_list: List[str], name: str = "") -> Callable:
    """Average outputs of multiple torch_* methods."""
    def _ens(d, m):
        # Lazy import METHODS to avoid circular
        try:
            from .methods import METHODS as _ALL
        except Exception:
            return {}
        all_scores = []
        for mname in method_list:
            if mname not in _ALL:
                continue
            fn, _f, _t, _n = _ALL[mname]
            if getattr(fn, "_unavailable_reason", None):
                continue
            try:
                s = fn(d, m)
                if s:
                    all_scores.append(s)
            except Exception:
                continue
        if not all_scores:
            return {}
        avg = {n: float(np.mean([s.get(n, 0.0) for s in all_scores])) for n in range(1, m + 1)}
        return _normalize(avg, m)
    return _ens


score_ensemble_torch_rnn = _torch_ensemble(["torch_lstm_m", "torch_gru_m", "torch_bilstm"])
score_ensemble_torch_cnn = _torch_ensemble(["torch_cnn1d", "torch_tcn", "torch_wavenet", "torch_resnet1d"])
score_ensemble_torch_attn = _torch_ensemble(["torch_transformer", "torch_lstm_attn", "torch_self_attention"])
score_ensemble_torch_top5 = _torch_ensemble(["torch_lstm_m", "torch_cnn1d_deep", "torch_transformer", "torch_tcn", "torch_lstm_attn"])
score_ensemble_torch_diverse = _torch_ensemble(["torch_lstm_m", "torch_cnn1d", "torch_transformer", "torch_mlp", "torch_resnet1d"])
score_ensemble_torch_giant = _torch_ensemble(["torch_lstm_l", "torch_lstm_xl", "torch_transformer_xl", "torch_wavenet_deep", "torch_bigru_deep"])
score_ensemble_torch_hybrid = _torch_ensemble(["torch_cnn_lstm", "torch_cnn_transformer", "torch_lstm_transformer", "torch_dual_path"])
score_ensemble_torch_bayesian = _torch_ensemble(["torch_bayesian_mlp", "torch_bayesian_lstm", "torch_deep_ensemble_mlp"])
score_ensemble_torch_novel = _torch_ensemble(["torch_echo_state", "torch_phased_lstm", "torch_time2vec_lstm", "torch_gmlp", "torch_mlp_mixer"])
score_ensemble_torch_mega = _torch_ensemble([
    "torch_lstm_m", "torch_cnn1d_deep", "torch_transformer_xl",
    "torch_lstm_attn", "torch_cnn_lstm", "torch_resnet1d",
    "torch_wavenet_deep", "torch_dual_path", "torch_attention_pool"
])


# ===========================================================================
# Registry
# ===========================================================================

TORCH_ADVANCED_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    # LSTM variations
    "torch_lstm_xs":     (score_torch_lstm_xs,     "torch-rnn-gpu", True, "LSTM hidden=8 (GPU)"),
    "torch_lstm_l":      (score_torch_lstm_l,      "torch-rnn-gpu", True, "LSTM hidden=128 2-layer dropout (GPU)"),
    "torch_lstm_xl":     (score_torch_lstm_xl,     "torch-rnn-gpu", True, "LSTM hidden=256 3-layer (GPU)"),
    "torch_gru_xs":      (score_torch_gru_xs,      "torch-rnn-gpu", True, "GRU hidden=8 (GPU)"),
    "torch_gru_l":       (score_torch_gru_l,       "torch-rnn-gpu", True, "GRU hidden=128 2-layer (GPU)"),
    "torch_bigru":       (score_torch_bigru,       "torch-rnn-gpu", True, "BiGRU hidden=32 (GPU)"),
    "torch_bigru_deep":  (score_torch_bigru_deep,  "torch-rnn-gpu", True, "BiGRU hidden=64 2-layer (GPU)"),
    "torch_bilstm_deep": (score_torch_bilstm_deep, "torch-rnn-gpu", True, "BiLSTM hidden=64 2-layer (GPU)"),
    # CNN variations
    "torch_cnn_k3":      (score_torch_cnn1d_k3,    "torch-cnn-gpu", True, "CNN1D kernel=3 (GPU)"),
    "torch_cnn_k7":      (score_torch_cnn1d_k7,    "torch-cnn-gpu", True, "CNN1D kernel=7 (GPU)"),
    "torch_cnn_k15":     (score_torch_cnn1d_k15,   "torch-cnn-gpu", True, "CNN1D kernel=15 (GPU)"),
    "torch_multi_scale": (score_torch_multi_scale_cnn, "torch-cnn-gpu", True, "Multi-scale CNN 3,7,15 parallel (GPU)"),
    "torch_inception":   (score_torch_inception_1d, "torch-cnn-gpu", True, "Inception 1D 1/3/5/7 (GPU)"),
    "torch_squeeze_excite": (score_torch_squeeze_excite, "torch-cnn-gpu", True, "Squeeze-Excite 1D (GPU)"),
    "torch_dilated_cnn": (score_torch_dilated_cnn, "torch-cnn-gpu", True, "Dilated CNN 1/2/4/8/16 (GPU)"),
    "torch_wavenet_deep": (score_torch_wavenet_deep, "torch-cnn-gpu", True, "WaveNet deep 6 dilations (GPU)"),
    # Transformer variations
    "torch_trans_narrow": (score_torch_transformer_narrow, "torch-transformer-gpu", True, "Transformer d=16 narrow (GPU)"),
    "torch_trans_wide":   (score_torch_transformer_wide,   "torch-transformer-gpu", True, "Transformer d=128 wide (GPU)"),
    "torch_trans_xl":     (score_torch_transformer_xl,     "torch-transformer-gpu", True, "Transformer d=64 6-layer XL (GPU)"),
    "torch_self_attention": (score_torch_self_attention,   "torch-transformer-gpu", True, "Pure self-attention only (GPU)"),
    # MLP variations
    "torch_mlp_narrow":  (score_torch_mlp_narrow,  "torch-mlp-gpu", True, "MLP (16,8) narrow (GPU)"),
    "torch_mlp_wide":    (score_torch_mlp_wide,    "torch-mlp-gpu", True, "MLP (256,128) wide (GPU)"),
    "torch_mlp_5layer":  (score_torch_mlp_5layer,  "torch-mlp-gpu", True, "MLP 5-layer (GPU)"),
    "torch_mlp_dropout": (score_torch_mlp_dropout, "torch-mlp-gpu", True, "MLP with dropout (GPU)"),
    "torch_gmlp":        (score_torch_gmlp,        "torch-mlp-gpu", True, "Gated MLP (gMLP) (GPU)"),
    "torch_mlp_mixer":   (score_torch_mlp_mixer,   "torch-mlp-gpu", True, "MLP-Mixer 1D (GPU)"),
    "torch_highway":     (score_torch_highway,     "torch-mlp-gpu", True, "Highway Network (GPU)"),
    # Hybrid
    "torch_cnn_lstm":    (score_torch_cnn_lstm,    "torch-hybrid-gpu", True, "CNN + LSTM stack (GPU)"),
    "torch_cnn_trans":   (score_torch_cnn_transformer, "torch-hybrid-gpu", True, "CNN + Transformer (GPU)"),
    "torch_lstm_trans":  (score_torch_lstm_transformer, "torch-hybrid-gpu", True, "LSTM + Transformer (GPU)"),
    "torch_dual_path":   (score_torch_dual_path,   "torch-hybrid-gpu", True, "Parallel CNN+LSTM dual path (GPU)"),
    # Bayesian / MC Dropout
    "torch_bayesian_mlp": (score_torch_bayesian_mlp,    "torch-bayesian-gpu", True, "Bayesian MLP (MC dropout, T=10) (GPU)"),
    "torch_bayesian_lstm": (score_torch_bayesian_lstm,  "torch-bayesian-gpu", True, "Bayesian LSTM with dropout (GPU)"),
    "torch_deep_ens_mlp": (score_torch_deep_ensemble_mlp, "torch-bayesian-gpu", True, "Deep Ensemble 5x MLP (GPU)"),
    # Novel
    "torch_echo_state":  (score_torch_echo_state,  "torch-novel-gpu", True, "Echo State Network reservoir (GPU)"),
    "torch_conv_lstm":   (score_torch_conv_lstm,   "torch-novel-gpu", True, "ConvLSTM 1D (GPU)"),
    "torch_phased_lstm": (score_torch_phased_lstm, "torch-novel-gpu", True, "Phased LSTM + time feature (GPU)"),
    "torch_time2vec":    (score_torch_time2vec_lstm, "torch-novel-gpu", True, "Time2Vec + LSTM (GPU)"),
    "torch_attn_pool":   (score_torch_attention_pool, "torch-novel-gpu", True, "Attention-weighted pooling (GPU)"),
    "torch_cross_attn":  (score_torch_cross_attention, "torch-novel-gpu", True, "Cross-attention chunks (GPU)"),
    # Ensembles of torch_* methods
    "ens_torch_rnn":     (score_ensemble_torch_rnn, "ensemble-gpu", False, "Ensemble: LSTM+GRU+BiLSTM (GPU)"),
    "ens_torch_cnn":     (score_ensemble_torch_cnn, "ensemble-gpu", False, "Ensemble: CNN+TCN+WaveNet+ResNet (GPU)"),
    "ens_torch_attn":    (score_ensemble_torch_attn, "ensemble-gpu", False, "Ensemble: Transformer+AttnLSTM+SelfAttn (GPU)"),
    "ens_torch_top5":    (score_ensemble_torch_top5, "ensemble-gpu", False, "Ensemble: top-5 GPU methods"),
    "ens_torch_diverse": (score_ensemble_torch_diverse, "ensemble-gpu", False, "Ensemble: 1 per family (GPU)"),
    "ens_torch_giant":   (score_ensemble_torch_giant, "ensemble-gpu", False, "Ensemble: largest models GPU"),
    "ens_torch_hybrid":  (score_ensemble_torch_hybrid, "ensemble-gpu", False, "Ensemble: all hybrid (GPU)"),
    "ens_torch_bayesian": (score_ensemble_torch_bayesian, "ensemble-gpu", False, "Ensemble: Bayesian methods (GPU)"),
    "ens_torch_novel":   (score_ensemble_torch_novel, "ensemble-gpu", False, "Ensemble: novel architectures (GPU)"),
    "ens_torch_mega":    (score_ensemble_torch_mega, "ensemble-gpu", False, "Ensemble: MEGA 9-way GPU"),
}
