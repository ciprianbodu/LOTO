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

from process_lock import file_lock
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


# Snapshots written before bounds were recorded used the inclusive interval 10–40.
_LEGACY_LR_RANGE = (10, 40)


def report_restricted_range(settings, result=None):
    """Read both saved bounds. Missing legacy bounds mean 10–40; conflicting metadata is unusable."""
    bounds = None
    for record in (settings, result or {}):
        if "restricted_range" not in record:
            continue
        pair = record["restricted_range"]
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(type(limit) is not int for limit in pair) or not 1 <= pair[0] <= pair[1]
                or (bounds is not None and tuple(pair) != bounds)):
            return None
        bounds = tuple(pair)
    return bounds if bounds is not None else _LEGACY_LR_RANGE


def mail_pool_alternative_lines(alternatives, normal, *, has_joker):
    """Render the inverse selection using the bounds recorded with the result."""
    if not isinstance(alternatives, dict) or "inverse_reduced" not in alternatives:
        return []
    title = "Pool invers · baza redusă"
    item = alternatives["inverse_reduced"]
    if not isinstance(item, dict):
        return [title, "  Indisponibil. Datele acestei selecții sunt incomplete."]
    from loto_enterprise.core.ro_text import count
    try:
        low, high = item.get("number_range") or item.get("inverse_range") or _LEGACY_LR_RANGE
        if type(low) is not int or type(high) is not int or not 1 <= low <= high:
            raise ValueError("Interval invalid.")
        title += f" {low}–{high} inclusiv"
        if error := item.get("error"):
            return [title, f"  Indisponibil. {error}"]
        pool = sorted(int(n) for n in item["pool"])
        code, eligible = int(item["code"]), int(item["eligible_count"])
        if (not pool or eligible < len(pool) or len(set(pool)) != len(pool)
                or pool[0] < low or pool[-1] > high
                or set(pool).intersection(int(n) for n in normal.get("hard_core", ()))):
            return [title, f"  Indisponibil. Numerele principale nu formează un pool invers valid în intervalul {low}–{high}."]
        lines = [
            f"{title} · Cod de evaluare {code} · {count(eligible, 'numere eligibile', 'număr eligibil')}",
            "  Numere principale · " + " ".join(map(str, pool)),
        ]
        if has_joker:
            bonus = [int(n) for n in item["jokers"]]
            eligible_bonus = int(item["eligible_joker_count"])
            normal_bonus = normal.get("joker_pool") or normal.get("hard_core_joker") or ()
            if (not bonus or len(set(bonus)) != len(bonus) or eligible_bonus < len(bonus)
                    or set(bonus).intersection(int(n) for n in normal_bonus)):
                return [title, "  Indisponibil. Pool-ul invers al Urnei 2 Joker este incomplet sau invalid."]
            lines.append(
                f"  Urna 2 Joker · {' '.join(map(str, bonus))} · "
                f"{count(eligible_bonus, 'bile eligibile', 'bilă eligibilă')}"
            )
        for ticket_no, zones in enumerate(item.get("tickets") or (), 1):
            for index, zone in enumerate(zones):
                numbers = sorted(int(n) for n in zone["numbers"])
                if not numbers or not set(numbers).issubset(pool):
                    return [title, "  Indisponibil. O zonă conține numere neconfirmate pentru acest pool."]
                line = f"  Bilet {ticket_no} · zona {chr(ord('A') + index)} · Cod LR {int(zone['code'])}"
                if numbers != pool:
                    line += " · " + " ".join(map(str, numbers))
                if has_joker:
                    ball = int(zone["joker"])
                    if ball not in bonus:
                        return [title, "  Indisponibil. Bila Joker a zonei lipsește din pool-ul Urnei 2."]
                    line += f" · bila Joker {ball}"
                lines.append(line)
        if item.get("budget_lei") is not None:
            lines.append(f"  Bugetul recomandării · {item['budget_lei']} lei")
        return lines
    except (KeyError, TypeError, ValueError):
        return [title, "  Indisponibil. Datele acestei selecții sunt incomplete."]


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
    text = (
        data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    )
    return text.splitlines(keepends=True)[-n_lines:]


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
# Decode / encode rezultat job (pickle+zstd+b64; fallback pickle+b64)
# --------------------------------------------------------------------------- #
ENCODING_PICKLE_B64 = "pickle+b64"
ENCODING_PICKLE_ZSTD_B64 = "pickle+zstd+b64"


def pack_queue_result(payload: object) -> str:
    """Serializează rezultatul jobului, preferând zstd din biblioteca standard.

    Worker-ul și UI-ul rulează normal pe 3.15, dar o defecțiune a modulului
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
    """Citește rezultate produse de worker în coada locală de încredere.

    Un rezultat încă nescris sau anulat poate avea payload gol ('{}').
    """
    try:
        data = json.loads(result_json)
    except (TypeError, ValueError, RecursionError) as exc:
        logger.warning(
            "[JOB] Rezultatul stocat nu este JSON valid (%s).", type(exc).__name__
        )
        return None
    if not isinstance(data, dict):
        logger.warning("[JOB] Rezultatul stocat nu este un obiect JSON.")
        return None
    payload = str(data.get("payload", ""))
    if not payload:
        return None
    enc = str(data.get("encoding", ENCODING_PICKLE_B64))
    try:
        blob = base64.b64decode(payload)
    except ValueError as exc:
        logger.warning(
            "[JOB] Datele rezultatului nu sunt Base64 valid (%s).",
            type(exc).__name__,
        )
        return None
    try:
        if enc == ENCODING_PICKLE_ZSTD_B64:
            from compression import zstd

            blob = zstd.decompress(blob)
        elif enc != ENCODING_PICKLE_B64:
            logger.warning("[JOB] Format necunoscut pentru rezultatul stocat.")
            return None
        return pickle.loads(blob)
    except Exception as exc:  # noqa: BLE001 — rezultatele istorice pot conține tipuri externe
        logger.warning(
            "[JOB] Rezultatul stocat nu poate fi decodat (%s).", type(exc).__name__
        )
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





# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
# Opțiunea Python -m/-c, și lipită de flaguri fără valoare (`-midlelib`, `-Bm`,
# `-Ic`): tot ce urmează e argv-ul modulului sau al comenzii, nu scriptul rulat.
# Nu prinde -X…/-W… (valoarea lor poate conține „c”/„m”) și nici --opțiuni lungi.
_PY_MODULE_OR_CMD = re.compile(r"-[bBdEhiIOPqRsSuvVx]*[cm]")


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
            # Numai un interpretor Python poate fi workerul: un editor sau un
            # shell deschis pe worker.py are aceeași cale în argv și bloca
            # repornirea workerului mort (audit 2026-10-22b). Prefix, nu listă
            # fixă: pe Linux numele e și „python3.15”. Fără nume: regula veche.
            name = str(proc.info.get("name") or "").lower()
            if name and not name.startswith("python"):
                continue
            cmdline = proc.info.get("cmdline") or []
            for i, arg in enumerate(cmdline):
                arg_s = str(arg)
                if i and _PY_MODULE_OR_CMD.match(arg_s):
                    # IDLE („Edit with IDLE”) rulează ca Python:
                    # `pythonw.exe -m idlelib worker.py` nu e workerul.
                    break
                norm_arg = os.path.normcase(arg_s)
                if "worker.py" not in norm_arg:
                    continue
                if norm_arg == target_norm:
                    return True
                if not os.path.isabs(arg_s):
                    # Relativ la directorul PROCESULUI, nu al UI-ului: un
                    # `python worker.py` din alt checkout nu e workerul nostru.
                    try:
                        arg_s = os.path.join(proc.cwd(), arg_s)
                    except (psutil.Error, OSError):
                        continue
                try:
                    if os.path.samefile(arg_s, WORKER_PATH):
                        return True
                except OSError:
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False


# Cooldown: _tick cheamă ensure o dată pe secundă; fără pauză, un spawn lent
# poate crea procese temporare redundante înainte de revendicarea cozii.
_WORKER_SPAWN_TS = 0.0
_WORKER_SPAWN_COOLDOWN_S = 15.0


def ensure_worker_running() -> None:
    global _WORKER_SPAWN_TS
    from job_queue import DB_PATH
    target = os.path.realpath(DB_PATH) + ".worker"
    try:
        with file_lock(target, timeout=0):
            pass
    except TimeoutError:
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
