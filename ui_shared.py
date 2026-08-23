"""Helpere neutre față de framework-ul de UI (NiceGUI: app_nicegui.py).

Conțin DOAR logică pură (decode rezultat job, citire/curățare loguri, lansare
worker) — fără import de nicegui — ca UI-ul să le poată folosi identic.
Backend-ul real (job_queue, worker, engine) rămâne neatins.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import pickle
import re
import subprocess
import sys
import time
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

LOG_FILE = "loto.log"

# Versiune Python țintă (ALF-LUPTATORI). ACTUALIZARI.bat / START_8000.bat folosesc py -3.14.
PYTHON_MIN = (3, 14)
PYTHON_TARGET_PATCH = 6  # 3.14.6 — informativ în mesaje


def check_python_version(*, strict: bool = False) -> tuple[bool, str]:
    """Verifică că interpretorul e Python 3.14+ (recomandat 3.14.6).

    Returns (ok, message). strict=True → eșuează dacă patch-ul e sub țintă.
    """
    vi = sys.version_info
    if vi < PYTHON_MIN:
        return False, (
            f"Python {vi.major}.{vi.minor}.{vi.micro} detectat — necesar "
            f">= {PYTHON_MIN[0]}.{PYTHON_MIN[1]}. "
            f"Ruleaza ACTUALIZARI.bat (py -3.14) sau instaleaza Python 3.14.6."
        )
    if strict and (vi.major, vi.minor, vi.micro) < (*PYTHON_MIN, PYTHON_TARGET_PATCH):
        return False, (
            f"Python {vi.major}.{vi.minor}.{vi.micro} — recomandat "
            f"{PYTHON_MIN[0]}.{PYTHON_MIN[1]}.{PYTHON_TARGET_PATCH}. "
            f"Ruleaza ACTUALIZARI.bat pentru upgrade venv."
        )
    return True, f"Python {vi.major}.{vi.minor}.{vi.micro} OK (tinta 3.14.{PYTHON_TARGET_PATCH})"


def require_python_version(*, strict: bool = False) -> None:
    ok, msg = check_python_version(strict=strict)
    if not ok:
        raise RuntimeError(msg)


import html as _html_module
from string.templatelib import Interpolation, Template


_CONVERSIONS = {"a": ascii, "r": repr, "s": str}


def render_html_safe(tmpl: Template) -> str:
    """Procesează t-string (PEP 750) cu escape HTML pe interpolări dinamice.

    Spre deosebire de f-string-uri, la t-string-uri `format_spec` și `conversion`
    NU se aplică singure — sunt doar metadate pe Interpolation, iar procesorul
    trebuie să le aplice explicit. Fără `format()`, un `{x:.1f}` se randa cu toată
    coada binară a float-ului (0.5244800000000001 în loc de 0.5).
    """
    parts: list[str] = []
    for piece in tmpl:
        if isinstance(piece, Interpolation):
            value = piece.value
            if piece.conversion:
                value = _CONVERSIONS[piece.conversion](value)
            text = format(value, piece.format_spec or "")
            parts.append(_html_module.escape(text, quote=True))
        else:
            parts.append(piece)
    return "".join(parts)


def html_escape(value: object) -> str:
    """Escape HTML pentru fragmente asamblate manual (ex. heatmap, chips)."""
    return _html_module.escape(str(value), quote=True)

PROJECT_ROOT = Path(__file__).resolve().parent
WORKER_PATH = PROJECT_ROOT / "worker.py"


# ---------------------------------------------------------------------------
# E-mail rezultate (SMTP) — OPȚIONAL. Config în mail_config.json (lângă proiect,
# gitignored) sau env LOTO_SMTP_USER / LOTO_SMTP_PASS / LOTO_MAIL_TO.
# Pentru Gmail e nevoie de o PAROLĂ DE APLICAȚIE (16 caractere, cu 2FA activ),
# NU parola contului. Vezi mail_config.json.example.
# ---------------------------------------------------------------------------
def load_mail_config(project_root=PROJECT_ROOT):
    """Întoarce config SMTP (dict) sau None dacă lipsesc user/parola/destinatar."""
    cfg = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_pass": "",
        "mail_to": "ciprianbodu@gmail.com",
        "tls_insecure": False,  # true DOAR dacă AV-ul interceptează SSL și strică verificarea
    }
    f = Path(project_root) / "mail_config.json"
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[mail] mail_config.json invalid: %s", exc)
    cfg["smtp_user"] = os.environ.get("LOTO_SMTP_USER", cfg.get("smtp_user") or "")
    # Gmail app password se afișează cu spații (4×4) — le scoatem (altfel login eșuează).
    cfg["smtp_pass"] = (os.environ.get("LOTO_SMTP_PASS", cfg.get("smtp_pass") or "") or "").replace(" ", "")
    cfg["mail_to"] = os.environ.get("LOTO_MAIL_TO", cfg.get("mail_to") or "")
    if not cfg["smtp_user"] or not cfg["smtp_pass"] or not cfg["mail_to"]:
        return None
    return cfg


def send_email(cfg, subject, body, attachments=None):
    """Trimite un e-mail text prin SMTP (STARTTLS). `attachments` = listă de căi de fișier."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = cfg["smtp_user"]
    msg["To"] = cfg["mail_to"]
    msg["Subject"] = subject
    msg.set_content(body or "(fără conținut)")
    for path in (attachments or []):
        try:
            p = Path(path)
            if p.exists():
                msg.add_attachment(p.read_bytes(), maintype="text", subtype="plain",
                                   filename=p.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[mail] atașament %s eșuat: %s", path, exc)
    # TLS VERIFICAT implicit (sigur). Verificarea se sare DOAR dacă utilizatorul a setat
    # EXPLICIT "tls_insecure": true în mail_config.json — decizie conștientă a lui (ex.
    # antivirus care interceptează SSL cu un cert pe care OpenSSL 3.x îl respinge).
    if cfg.get("tls_insecure"):
        ctx = ssl._create_unverified_context()
        logger.warning("[mail] tls_insecure=true → trimitere FĂRĂ verificare de certificat TLS.")
    else:
        ctx = ssl.create_default_context()
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as s:
        s.starttls(context=ctx)
        s.login(cfg["smtp_user"], cfg["smtp_pass"])
        s.send_message(msg)


# --------------------------------------------------------------------------- #
# Loguri
# --------------------------------------------------------------------------- #
def read_logs_filtered(n_lines: int = 50) -> str:
    if not os.path.exists(LOG_FILE):
        return "Nu există log-uri încă."
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        recent = lines[-n_lines:]
        filtered: list[str] = []
        pattern = re.compile(r"Iteratia \d+: Acoperite \d+/\d+")
        similar = 0
        for line in recent:
            if pattern.search(line):
                similar += 1
                if similar <= 3:
                    filtered.append(line)
                elif similar == 4:
                    filtered.append("... [mesaje de progres ascunse pentru claritate] ...\n")
            else:
                similar = 0
                filtered.append(line)
        return "".join(filtered).strip()
    except Exception as e:  # noqa: BLE001
        return f"Eroare citire log: {e}"


def clear_logs() -> None:
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] --- Log curățat manual ---\n")
    except OSError as exc:
        logger.warning("clear_logs: nu am putut rescrie %s: %s", LOG_FILE, exc)


# --------------------------------------------------------------------------- #
# Decode / encode rezultat job (pickle+zstd+b64 pe 3.14; fallback pickle+b64)
# --------------------------------------------------------------------------- #
ENCODING_PICKLE_B64 = "pickle+b64"
ENCODING_PICKLE_ZSTD_B64 = "pickle+zstd+b64"


def pack_queue_result(payload: object) -> str:
    """Serializează rezultatul jobului: pickle → zstd (PEP 784) → base64."""
    from compression import zstd

    raw = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    compressed = zstd.compress(raw, 3)
    return json.dumps({
        "encoding": ENCODING_PICKLE_ZSTD_B64,
        "payload": base64.b64encode(compressed).decode("ascii"),
    })


def decode_queue_result(result_json: str) -> object:
    """La cancel-race worker-ul poate scrie payload gol ('{}'); întoarcem None
    și apelantul tratează non-tuple ca rezultat invalid."""
    try:
        data = json.loads(result_json)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    payload = str(data.get("payload", ""))
    if not payload:
        return None
    enc = str(data.get("encoding", ENCODING_PICKLE_B64))
    try:
        blob = base64.b64decode(payload)
    except Exception:  # noqa: BLE001
        return None
    try:
        if enc == ENCODING_PICKLE_ZSTD_B64:
            from compression import zstd
            blob = zstd.decompress(blob)
        elif enc != ENCODING_PICKLE_B64:
            return None
        return pickle.loads(blob)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Scriere atomică (anti-corupere: crash/sleep/sync OneDrive la mijlocul scrierii)
# --------------------------------------------------------------------------- #
def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Scrie în <file>.tmp (flush+fsync) apoi os.replace — atomic pe Win+POSIX.
    Cititorii văd fie versiunea veche completă, fie cea nouă completă."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def atomic_write_json(path, obj, *, indent: int = 2, ensure_ascii: bool = False) -> None:
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii))


class file_lock:
    """Lock advisory cross-proces (Win+POSIX) prin lock-file O_EXCL, cu timeout.
    Previne lost-updates când worker-ul și UI-ul fac read-modify-write pe același
    fișier (ex. adaptive_state.json). La timeout continuă fără lock (anti-deadlock
    pe lock-uri stale), pentru că scrierea în sine e oricum atomică."""

    def __init__(self, target, timeout: float = 10.0):
        self.lockpath = str(target) + ".lock"
        self.timeout = timeout
        self._fd = None

    def __enter__(self):
        start = time.time()
        while True:
            try:
                self._fd = os.open(self.lockpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.time() - start > self.timeout:
                    logger.debug("[file_lock] timeout pe %s — continui fără lock", self.lockpath)
                    return self
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        try:
            os.unlink(self.lockpath)
        except OSError:
            pass
        return False


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
def is_worker_running() -> bool:
    root = str(PROJECT_ROOT)
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue
            cmd = " ".join(str(p) for p in cmdline)
            if "worker.py" in cmd and root in cmd:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False


def ensure_worker_running() -> None:
    if is_worker_running():
        return
    lock_path = PROJECT_ROOT / ".worker_spawn.lock"
    fd = None
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        time.sleep(0.4)
        if is_worker_running():
            return
        try:
            if time.time() - lock_path.stat().st_mtime > 15:
                lock_path.unlink(missing_ok=True)
        except OSError:
            return
        return
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        if is_worker_running():
            return
        subprocess.Popen(
            [sys.executable, str(WORKER_PATH)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=(os.name != "nt"),
        )
        logger.info("[ui_shared] worker.py lansat.")
    except Exception as exc:  # noqa: BLE001
        logger.error("[ui_shared] nu pot lansa worker.py: %s", exc)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass
