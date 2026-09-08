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
import uuid
from pathlib import Path

import psutil

from runtime_paths import ENGINE_LOG_FILE, PROJECT_ROOT

logger = logging.getLogger(__name__)
LOG_FILE = str(ENGINE_LOG_FILE)

# `logger` e IMPORTAT atât de app_nicegui.py cât și de worker.py, dar cele două
# procese configurează root logging DIFERIT: worker.py atașează explicit un
# FileHandler pe LOG_FILE, în timp ce app_nicegui.py face doar
# `logging.basicConfig(...)` FĂRĂ `handlers=` (deci implicit doar StreamHandler pe
# stderr). Un `logger.error(...)` emis de AICI din procesul UI (ex.
# `ensure_worker_running` eșuat — exact diagnosticul de care ai nevoie când
# joburile rămân PENDING) era deci invizibil în panoul de log al UI-ului, care
# citește/curăță exclusiv LOG_FILE. Atașăm propriul FileHandler necondiționat de
# configurația root a apelantului; `propagate = False` evită scrierea DUBLĂ în
# LOG_FILE când apelantul e worker.py (care oricum are propriul FileHandler pe root).
logger.propagate = False
if not logger.handlers:
    try:
        _fh = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
        _fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
        logger.addHandler(_fh)
        logger.setLevel(logging.INFO)
    except OSError:
        pass  # ex. directorul de runtime nu există încă la primul import

# Versiune Python țintă (ALF-LUPTATORI). ACTUALIZARI.bat / START_8000.bat folosesc py -3.14.
PYTHON_MIN = (3, 14)


def check_python_version(*, strict: bool = False) -> tuple[bool, str]:
    """Verifică Python 3.14+; patch-ul curent este gestionat de ACTUALIZARI.bat.

    Nu hardcodăm patch-ul aici: updater-ul interoghează python.org și instalează
    ultimul 3.14.x stabil. `strict=True` respinge doar build-urile pre-release.
    """
    vi = sys.version_info
    if vi < PYTHON_MIN:
        return False, (
            f"Python {vi.major}.{vi.minor}.{vi.micro} detectat — necesar "
            f">= {PYTHON_MIN[0]}.{PYTHON_MIN[1]}. "
            "Ruleaza ACTUALIZARI.bat pentru ultimul Python 3.14.x stabil."
        )
    if strict and vi.releaselevel != "final":
        return False, (
            f"Python {vi.major}.{vi.minor}.{vi.micro} {vi.releaselevel} — "
            "este necesar un release stabil. Ruleaza ACTUALIZARI.bat."
        )
    return True, (
        f"Python {vi.major}.{vi.minor}.{vi.micro} OK "
        "(ACTUALIZARI.bat menține ultimul patch stabil 3.14.x)"
    )


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
    cfg["smtp_pass"] = (
        os.environ.get("LOTO_SMTP_PASS", cfg.get("smtp_pass") or "") or ""
    ).replace(" ", "")
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
    for path in attachments or []:
        try:
            p = Path(path)
            if p.exists():
                msg.add_attachment(
                    p.read_bytes(), maintype="text", subtype="plain", filename=p.name
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[mail] atașament %s eșuat: %s", path, exc)
    # TLS VERIFICAT implicit (sigur). Verificarea se sare DOAR dacă utilizatorul a setat
    # EXPLICIT "tls_insecure": true în mail_config.json — decizie conștientă a lui (ex.
    # antivirus care interceptează SSL cu un cert pe care OpenSSL 3.x îl respinge).
    if cfg.get("tls_insecure"):
        ctx = ssl._create_unverified_context()
        logger.warning(
            "[mail] tls_insecure=true → trimitere FĂRĂ verificare de certificat TLS."
        )
    else:
        ctx = ssl.create_default_context()
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=30) as s:
        s.starttls(context=ctx)
        s.login(cfg["smtp_user"], cfg["smtp_pass"])
        s.send_message(msg)


# --------------------------------------------------------------------------- #
# Loguri
# --------------------------------------------------------------------------- #
# Cât citim de la COADA fișierului ca să acoperim `n_lines`. O linie de log are
# ~100-150 octeți, deci 256 KB acoperă ~2000 de linii — de peste 10× n_lines-ul
# maxim cerut de UI (120).
_LOG_TAIL_BYTES = 256 * 1024


def read_tail_lines(path: str, n_lines: int, block: int = _LOG_TAIL_BYTES) -> list[str]:
    """Ultimele `n_lines` linii, citind DOAR coada fișierului.

    `f.readlines()` citea TOT fișierul ca să păstreze ultimele n linii. Înainte
    de mutarea logului din OneDrive, măsuram 241 ms/apel la 50 MB și 24.7 ms la
    5 MB, de la un tick UI pe secundă. Cu seek pe coadă: ~0.6-1 ms, indiferent
    de dimensiune. Rezultatul e IDENTIC cât timp blocul acoperă n_lines (vezi
    `_LOG_TAIL_BYTES`); dacă nu, întoarce câte linii încap — degradare grațioasă,
    nu eroare.
    """
    if n_lines <= 0:
        # `list[-0:]` == `list[0:]` (tot fișierul) în Python, fiindcă -0 == 0 —
        # fără gardă explicită, n_lines=0 întorcea totul, nu nimic.
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - block))
        data = f.read()
    if size > block:
        # prima linie e aproape sigur tăiată la mijloc → o aruncăm
        nl = data.find(b"\n")
        if nl != -1:
            data = data[nl + 1 :]
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)[-n_lines:]


def read_logs_filtered(n_lines: int = 50) -> str:
    if not os.path.exists(LOG_FILE):
        return "Nu există log-uri încă."
    try:
        recent = read_tail_lines(LOG_FILE, n_lines)
        filtered: list[str] = []
        pattern = re.compile(r"Iteratia \d+: Acoperite \d+/\d+")
        similar = 0
        for line in recent:
            if pattern.search(line):
                similar += 1
                if similar <= 3:
                    filtered.append(line)
                elif similar == 4:
                    filtered.append(
                        "... [mesaje de progres ascunse pentru claritate] ...\n"
                    )
            else:
                similar = 0
                filtered.append(line)
        return "".join(filtered).strip()
    except Exception as e:  # noqa: BLE001
        return f"Eroare citire log: {e}"


def clear_logs() -> None:
    """Golește LOG_FILE, cu un header nou.

    NU elimină cursa cu un worker care scrie ACTIV pe fișier (logging.FileHandler
    al worker.py e deschis în append pentru toată durata procesului și nu participă
    la niciun lock din acest modul) — o eliminare completă ar cere ca AMBII
    scriitori să treacă prin același mecanism de coordonare, schimbare mai mare
    decât acest fix. `file_lock` protejează totuși împotriva a doi apelanți
    CONCURENȚI ai lui `clear_logs()` însuși (ex. dublu-click în UI)."""
    header = (
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [INFO] --- Log curățat manual ---\n"
    )
    try:
        with file_lock(LOG_FILE, timeout=2.0):
            if not os.path.exists(LOG_FILE):
                # "r+" cere fișierul deja EXISTENT (spre deosebire de vechiul "w",
                # care îl crea) — la prima rulare (niciun log scris încă) am cădea
                # altfel în except de mai jos în loc să creăm fișierul.
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write(header)
                return
            with open(LOG_FILE, "r+", encoding="utf-8") as f:
                f.seek(0)
                f.write(header)
                f.truncate()
    except OSError as exc:
        logger.warning("clear_logs: nu am putut rescrie %s: %s", LOG_FILE, exc)


# --------------------------------------------------------------------------- #
# Decode / encode rezultat job (pickle+zstd+b64 pe 3.14; fallback pickle+b64)
# --------------------------------------------------------------------------- #
ENCODING_PICKLE_B64 = "pickle+b64"
ENCODING_PICKLE_ZSTD_B64 = "pickle+zstd+b64"


def pack_queue_result(payload: object) -> str:
    """Serializează rezultatul jobului, preferând zstd din Python 3.14.

    Worker-ul și UI-ul rulează normal pe 3.14, dar o defecțiune a modulului
    opțional de compresie nu trebuie să transforme un job calculat corect într-un
    rezultat imposibil de citit. Formatul legacy ``pickle+b64`` rămâne acceptat
    de decoder tocmai pentru această cale de continuitate.
    """
    raw = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    try:
        from compression import zstd

        compressed = zstd.compress(raw, 3)
    except Exception as exc:  # noqa: BLE001 — degradare intenționată la orice defecțiune
        # de compresie, nu doar import lipsă: un eșec la ÎNSUȘI `zstd.compress()`
        # (ex. eroare specifică zstd, MemoryError pe un payload patologic de mare)
        # trebuia să cadă pe fallback la fel ca un modul lipsă — altfel un job
        # calculat corect pica la excepție NEPRINSĂ aici, deși docstring-ul de mai
        # sus promite exact contrariul.
        logger.warning(
            "Compresie zstd indisponibilă/eșuată pentru rezultatul jobului; "
            "folosesc pickle+b64: %s",
            exc,
        )
        return json.dumps(
            {
                "encoding": ENCODING_PICKLE_B64,
                "payload": base64.b64encode(raw).decode("ascii"),
            }
        )
    return json.dumps(
        {
            "encoding": ENCODING_PICKLE_ZSTD_B64,
            "payload": base64.b64encode(compressed).decode("ascii"),
        }
    )


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
    """Scrie într-un tmp cu nume UNIC (flush+fsync) apoi os.replace — atomic pe
    Win+POSIX. Cititorii văd fie versiunea veche completă, fie cea nouă completă.
    Numele tmp e unic per scriere (pid+uuid): cu un ".tmp" fix, doi scriitori
    concurenți pe același fișier își truncau reciproc tmp-ul și un torn file
    putea fi promovat „atomic" (cauza istorică a erorilor de tracker din loto.log)."""
    p = Path(path)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(
    path, obj, *, indent: int = 2, ensure_ascii: bool = False
) -> None:
    atomic_write_text(path, json.dumps(obj, indent=indent, ensure_ascii=ensure_ascii))


class file_lock:
    """Lock advisory cross-proces (Win+POSIX) prin lock-file O_EXCL, cu timeout.
    Previne lost-updates când worker-ul și UI-ul fac read-modify-write pe același
    fișier (ex. adaptive_state.json). La timeout continuă fără lock (anti-deadlock
    pe lock-uri stale), pentru că scrierea în sine e oricum atomică.

    Staleness pe VÂRSTA fișierului de lock (mtime), NU pe cât a așteptat acest
    waiter: un waiter sosit TÂRZIU pe un lock proaspăt (deținător activ, secțiune
    critică legitim mai lungă decât `timeout`) altfel îl considera stale după
    PROPRIUL cronometru, deși deținătorul abia începuse — spărgea un lock VIU.
    Token unic scris în fișier la creare, verificat la `__exit__` înainte de
    unlink: dacă alt proces a spart și recreat lock-ul între timp (ambii treceau
    de pragul de staleness aproape simultan), conținutul nu mai e al nostru și NU
    îl ștergem — altfel am fi șters lock-ul VIU al noului proprietar."""

    def __init__(self, target, timeout: float = 10.0):
        self.lockpath = str(target) + ".lock"
        self.timeout = timeout
        self._fd = None
        self._token: str | None = None

    def _lock_age(self) -> float:
        try:
            return time.time() - os.stat(self.lockpath).st_mtime
        except OSError:
            return 0.0  # dispărut chiar acum — nu-l tratăm ca stale

    def _create(self) -> bool:
        token = f"{os.getpid()}:{uuid.uuid4().hex}"
        try:
            fd = os.open(self.lockpath, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return False
        try:
            os.write(fd, token.encode("ascii"))
        except OSError:
            pass
        self._fd = fd
        self._token = token
        return True

    def __enter__(self):
        start = time.time()
        while True:
            if self._create():
                return self
            if self._lock_age() > self.timeout:
                logger.debug(
                    "[file_lock] lock stale pe %s (vârstă > %.1fs) — îl sparg",
                    self.lockpath,
                    self.timeout,
                )
                try:
                    os.unlink(self.lockpath)
                except OSError:
                    pass
                self._create()  # dacă eșuează din nou, continuăm fără lock mai jos
                return self
            if time.time() - start > self.timeout * 3:
                # Plasă finală anti-deadlock: chiar dacă lock-ul pare mereu
                # "proaspăt" (spart și recreat continuu de alți waiteri), nu
                # așteptăm la nesfârșit — scrierea în sine e oricum atomică.
                logger.debug(
                    "[file_lock] renunț la %s după %.1fs fără lock",
                    self.lockpath,
                    self.timeout * 3,
                )
                return self
            time.sleep(0.05)

    def __exit__(self, *exc):
        # Ștergem lock-file-ul DOAR dacă (a) l-am creat NOI (self._fd setat) ȘI
        # (b) conținutul lui e ÎNCĂ tokenul nostru. Fără (b): un al doilea proces
        # care a spart același lock stale aproape simultan (ambii treceau pragul
        # de staleness) l-a putut recrea DUPĂ noi — am fi șters lock-ul VIU al
        # noului proprietar, nu pe-al nostru (mutual exclusion spartă pentru toți
        # următorii, exact bug-ul pe care verificarea (a) singură îl rata).
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            try:
                with open(self.lockpath, "rb") as f:
                    current = f.read().decode("ascii", errors="replace")
                if current == self._token:
                    os.unlink(self.lockpath)
            except OSError:
                pass
        return False


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
def is_worker_running() -> bool:
    """Compară fiecare argument din cmdline cu `WORKER_PATH`, NU substring pe
    linia de comandă întreagă. Vechiul `root in cmd` (root = PROJECT_ROOT ca
    text) se potrivea și cu un proces al cărui cmdline avea PROJECT_ROOT doar
    ca PREFIX de string — de exemplu un worker pornit dintr-un checkout nested
    (`PROJECT_ROOT/.claude/worktrees/.../worker.py`) sau dintr-un director
    frate cu nume ce începe la fel (`D:\\_BUILD\\_LOTO` vs `D:\\_BUILD\\_LOTO_OLD`).
    `ensure_worker_running()` credea atunci că workerul ACESTUI proiect rulează,
    când de fapt rula altul — jobul submis rămânea PENDING la nesfârșit, fără
    nicio eroare vizibilă în UI.
    normcase: pe Windows căile din cmdline pot diferi doar prin CASE
    (d:\\_libraries vs D:\\_LIBRARIES); `samefile` acoperă și forme echivalente
    dar scrise diferit (relativ vs absolut, `%~dp0` din START_8000.bat).
    """
    target_norm = os.path.normcase(str(WORKER_PATH))
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            for arg in cmdline:
                arg_s = str(arg)
                norm_arg = os.path.normcase(arg_s)
                if "worker.py" not in norm_arg:
                    continue
                if norm_arg == target_norm:
                    return True
                try:
                    if os.path.samefile(arg_s, WORKER_PATH):
                        return True
                except OSError:
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False


# Cooldown: _tick cheamă ensure o dată pe secundă; fără pauză, un spawn lent
# produce un al doilea worker al cărui requeue de startup fură jobul RUNNING.
_WORKER_SPAWN_TS = 0.0
_WORKER_SPAWN_COOLDOWN_S = 15.0


def ensure_worker_running() -> None:
    global _WORKER_SPAWN_TS
    if is_worker_running():
        return
    now = time.time()
    if now - _WORKER_SPAWN_TS < _WORKER_SPAWN_COOLDOWN_S:
        return
    _WORKER_SPAWN_TS = now
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
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
