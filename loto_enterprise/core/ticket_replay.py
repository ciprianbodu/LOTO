"""„Bilet complet” refacut pe pasii walk-forward.

Fiecare pas WF pastreaza contextul biletului (`ticket_context`): pool-ul,
clasamentul metodei din audit, numarul Joker si extragerea tinta. Pentru
fiecare pas se reface ce ar fi dat „🎟️ Bilet complet” in ziua aceea si se
retine cel mai bun hit pe o varianta. Joker: numai Urna 1. Garantia este cea
a rezultatului afisat, ca la butonul din sidebar.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from loto_enterprise.core.full_ticket import build_full_ticket, clamp_tickets

logger = logging.getLogger(__name__)


def _silence_wheel_logs() -> None:
    logging.getLogger("covering.greedy").setLevel(logging.WARNING)


@contextmanager
def _quiet_wheel():
    """Fără [WHEEL] Progres pe fiecare dintre sutele de bilete refăcute."""
    log = logging.getLogger("covering.greedy")
    prev = log.level
    log.setLevel(logging.WARNING)
    try:
        yield
    finally:
        log.setLevel(prev)


def step_contexts(flat) -> dict[int, dict | None]:
    """Contextul fiecarei extrageri WF (None = intrare scrisa inainte de camp)."""
    out: dict[int, dict | None] = {}
    for row in flat or ():
        di = int(getattr(row, "draw_index", -1))
        if di not in out or out[di] is None:
            out[di] = getattr(row, "ticket_context", None)
    return out


def _tickets(game: str, context: dict, tickets: int, guarantee):
    """Variantele Urnei 1 ale biletului pasului, sau textul erorii."""
    data = {
        "hard_core": list(context["hard_core"]),
        "guarantee": guarantee,
        "audit": dict(context.get("audit") or {}),
        "hard_core_joker": list(context.get("hard_core_joker") or []),
    }
    t = build_full_ticket(game, data, tickets, with_chances=False)
    if t.get("error") or not t.get("variants"):
        return t.get("error") or "fără variante"
    if len(t["variants"]) < t["requested"]:
        # Pas fără clasament în context (pool din fallback-ul de frecvență): pool-ul
        # nu se poate extinde și dă mai puține variante decât biletele afișate.
        return "mai puține variante decât biletele cerute"
    cut = -1 if t.get("joker") is not None else None
    return [list(v[:cut]) for v in t["variants"]]


def replay_step(game: str, context: dict, tickets: int, guarantee) -> dict:
    """{"hits": h, "variants": n} = cele mai multe numere nimerite pe o varianta.

    {"error": text} cand biletul nu se poate construi (bilet nemodelat, pool
    prea mic, context incomplet)."""
    actual = {int(x) for x in context.get("actual") or []}
    if not actual or not context.get("hard_core"):
        return {"error": "context incomplet"}
    variants = _tickets(game, context, tickets, guarantee)
    if isinstance(variants, str):
        return {"error": variants}
    return {"hits": max(len(set(v) & actual) for v in variants), "variants": len(variants)}


def uniform_rates(game: str, context: dict, tickets: int, guarantee) -> dict | None:
    """P(cel putin o varianta >= t) exact, extragere uniforma, pe biletele pasului.

    Probabilitatea nu depinde de etichetele numerelor, ci de cum se suprapun
    variantele; pasii cu aceeasi geometrie au deci aceleasi valori."""
    from covering.spread import ticket_hit_probabilities
    from loto_enterprise.core.lotteries import lottery_by_id

    lot = lottery_by_id(game)
    if lot is None or not context or not context.get("hard_core"):
        return None
    variants = _tickets(game, context, tickets, guarantee)
    if isinstance(variants, str):
        return None
    try:
        return ticket_hit_probabilities(variants, lot.draw_n, lot.max_n)
    except ValueError:
        return None


def replay_chunk(game: str, items, tickets: int, guarantee) -> list[tuple[int, dict]]:
    return [(int(di), replay_step(game, ctx, tickets, guarantee)) for di, ctx in items]


def replay_full_tickets(flat, game: str, tickets: int, guarantee, workers: int = 1) -> dict:
    """Reluarea pe toti pasii cu context.

    `best` = {draw_index: {"hits": h, "variants": n}}; `missing` = pasi fara
    context (cache WF vechi); `errors` = {motiv: numar de pasi}."""
    tickets = clamp_tickets(tickets)
    contexts = step_contexts(flat)
    usable = sorted((di, ctx) for di, ctx in contexts.items() if ctx)
    missing = sum(1 for ctx in contexts.values() if ctx is None)
    with _quiet_wheel():
        return _replay_body(contexts, usable, missing, game, tickets, guarantee, workers)


def _replay_body(contexts, usable, missing, game, tickets, guarantee, workers):
    results: list[tuple[int, dict]] = []
    if workers > 1 and len(usable) > 64:
        from concurrent.futures import ProcessPoolExecutor

        n_chunks = workers * 4
        chunks = [usable[i::n_chunks] for i in range(n_chunks) if usable[i::n_chunks]]
        try:
            with ProcessPoolExecutor(
                max_workers=workers, initializer=_silence_wheel_logs
            ) as ex:
                futures = [
                    ex.submit(replay_chunk, game, chunk, tickets, guarantee)
                    for chunk in chunks
                ]
                for fut in futures:
                    results.extend(fut.result())
        except Exception as exc:  # noqa: BLE001
            logger.warning("[BILET] reluare paralela indisponibila (%s) — in proces", exc)
            results = []
    if not results:
        results = replay_chunk(game, usable, tickets, guarantee)
    best: dict = {}
    errors: dict[str, int] = {}
    for di, row in results:
        if "error" in row:
            errors[row["error"]] = errors.get(row["error"], 0) + 1
        else:
            best[di] = row
    last = max(best, default=None)
    return {
        "uniform": (
            uniform_rates(game, contexts[last], tickets, guarantee)
            if last is not None
            else None
        ),
        "uniform_draw": last,
        "tickets": tickets,
        "n_draws": len(contexts),
        "missing": missing,
        "unavailable": len(contexts) - missing - len(best),
        "errors": errors,
        "best": best,
    }
