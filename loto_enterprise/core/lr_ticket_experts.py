"""Full-information Fixed Share over already built Romanian LR portfolios."""

from __future__ import annotations

from collections import OrderedDict
from compression.zstd import ZstdError
from copy import deepcopy
from datetime import date
from functools import lru_cache
from hashlib import sha256
import json
import logging
import pickle
from math import exp, isfinite, log
from threading import Lock

import numpy as np


FIXED_SHARE_VERSION = "lr-ticket-fixed-share-v2"
# First entry is the baseline and wins ties. All settings are fixed before the outer evaluation.
TUNING_GRID = ((0.7, 0.05), (0.35, 0.02), (1.4, 0.10))
TUNING_WARMUP_DAYS = 26
ONLINE_TICKET_EXPERTS = "online_ticket_experts"
TRAINING_DAYS = 104
DEFAULT_EXPERTS = ("frequency", "compact_transition_prior", "markov_lag2",
                   "bayes_runlength_shrink", "temporal_stacked_calibration")
_OBJECTIVES = frozenset(("any_prize", "hit4", "hit5", "hit6"))


def fixed_share_context(settings) -> str:
    """Canonical signature of a portfolio's complete settings and source identity."""
    payload = json.dumps([FIXED_SHARE_VERSION, settings], sort_keys=True, allow_nan=False, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


class FixedShareTicketExperts:
    """Select one expert before each forecast day and only then learn all losses.

    The caller builds same-budget final plans from earlier draws. Each eligible
    draw has bounded loss 1 minus the selected criterion on its portfolio.
    One mean loss per completed day enters the update, even on a double day.
    """

    def __init__(self, experts, objective, *, learning_rate=0.7, share=0.05, context=None):
        self.experts = tuple(experts)
        if (
            not self.experts or len(set(self.experts)) != len(self.experts)
            or any(not isinstance(name, str) or not name for name in self.experts)
        ):
            raise ValueError("Experts must have distinct, nonempty names.")
        objective = "hit4" if objective == "four_plus" else objective
        if objective not in _OBJECTIVES:
            raise ValueError(f"Unsupported portfolio objective: {objective!r}")
        if not isfinite(learning_rate) or learning_rate <= 0 or not 0 <= share <= 1:
            raise ValueError("Fixed Share requires a positive rate and a share in [0,1].")
        self.objective = objective
        self.learning_rate = float(learning_rate)
        self.share = float(share)
        self.context = context
        self.weights = tuple(1 / len(self.experts) for _ in self.experts)
        self.last_day = None
        self.days = 0
        self.history_digest = sha256(b"").hexdigest()

    def choose(self):
        """Return the expert to play now; declaration order breaks ties."""
        return self.experts[max(range(len(self.experts)), key=self.weights.__getitem__)]

    def _update(self, losses):
        # Stable normalization also handles competitors already at zero weight.
        log_weights = [
            log(weight) - self.learning_rate * loss if weight > 0 else float("-inf")
            for weight, loss in zip(self.weights, losses, strict=True)
        ]
        peak = max(log_weights)
        updated = [exp(score - peak) for score in log_weights]
        normalizer = sum(updated)
        mixed = self.share / len(self.experts)
        self.weights = tuple((1 - self.share) * weight / normalizer + mixed for weight in updated)

    def observe(self, day, plans, draws):
        """Grade final tickets on one completed day and advance the checkpoint."""
        from loto_enterprise.core.lr_played_portfolio import expand_plan, grade_draw

        day = date.fromisoformat(str(day)).isoformat()
        if self.last_day is not None and day <= self.last_day:
            raise ValueError("A Fixed Share day must follow the previous completed day.")
        if plans.keys() != set(self.experts):
            raise ValueError("Every expert must supply exactly one final portfolio.")
        outcomes = [(tuple(numbers), joker) for numbers, joker in draws]
        if not outcomes:
            raise ValueError("A completed day must contain at least one draw.")
        chosen = self.choose()
        incoming = dict(zip(self.experts, self.weights, strict=True))
        if len({(plan["game"], plan.get("budget_bani")) for plan in plans.values()}) != 1:
            raise ValueError("Experts must forecast the same game and declared budget.")
        layouts = [expand_plan(plans[name]) for name in self.experts]
        grades = [[grade_draw(layout, numbers, joker, draw_date=day) for layout in layouts]
                  for numbers, joker in outcomes]
        criteria = [[score[self.objective] for score in group] for group in grades]
        # For a fair update all experts use the same complete observations.
        usable = [row for row in criteria if all(win is not None for win in row)]
        if usable:
            losses = [1 - sum(events) / len(usable) for events in zip(*usable, strict=True)]
            self._update(losses)
        else:
            losses = [None] * len(self.experts)
        self.last_day = day
        self.days += 1
        payload = json.dumps(
            [day, [[list(map(int, numbers)), None if joker is None else int(joker)] for numbers, joker in outcomes]],
            separators=(",", ":"),
        )
        self.history_digest = sha256((self.history_digest + payload).encode("ascii")).hexdigest()
        played_index = self.experts.index(chosen)
        return {
            "date": day, "played_expert": chosen, "incoming_weights": incoming,
            "weights": dict(zip(self.experts, self.weights, strict=True)),
            "losses": dict(zip(self.experts, losses, strict=True)),
            "evaluated": len(usable), "draws": len(outcomes),
            "played": [group[played_index] for group in grades],
        }

    def checkpoint(self):
        """Report all deterministic state required to resume the same prefix."""
        return {
            "version": FIXED_SHARE_VERSION, "context": self.context, "experts": list(self.experts),
            "objective": self.objective, "learning_rate": self.learning_rate, "share": self.share,
            "weights": list(self.weights), "last_day": self.last_day, "days": self.days,
            "history_digest": self.history_digest,
        }

    @classmethod
    def restore(cls, checkpoint, *, context, history_digest):
        """Resume only after the input context and consumed history are verified."""
        if (
            checkpoint.get("version") != FIXED_SHARE_VERSION or checkpoint.get("context") != context
            or checkpoint.get("history_digest") != history_digest
        ):
            raise ValueError("Fixed Share checkpoint does not match the current history and settings.")
        model = cls(checkpoint["experts"], checkpoint["objective"],
                    learning_rate=checkpoint["learning_rate"], share=checkpoint["share"], context=context)
        weights = tuple(checkpoint["weights"])
        days, last_day = checkpoint["days"], checkpoint["last_day"]
        if (
            len(weights) != len(model.experts) or any(not isfinite(w) or w < 0 for w in weights)
            or abs(sum(weights) - 1) > 1e-9 or type(days) is not int or days < 0
            or (last_day is None) != (days == 0)
            or (last_day is not None and date.fromisoformat(last_day).isoformat() != last_day)
        ):
            raise ValueError("Fixed Share checkpoint contains invalid weights or chronology.")
        model.weights = weights
        model.days = days
        model.last_day = last_day
        model.history_digest = history_digest
        return model


class _TunedTicketExperts:
    """Compete predeclared rates on complete past days using the actual chosen expert's loss."""

    def __init__(self, experts, objective, context):
        self.models = [FixedShareTicketExperts(experts, objective, learning_rate=rate, share=share, context=context)
                       for rate, share in TUNING_GRID]
        self.losses = [0.0] * len(self.models)
        self.evaluated_days = 0

    @property
    def selected(self):
        return self.models[min(range(len(self.models)), key=self.losses.__getitem__)]

    def observe(self, day, plans, outcomes):
        choices = [model.choose() for model in self.models]
        baseline = self.models[0]
        # Grade once; every policy observes the same underlying expert portfolios.
        record = baseline.observe(day, plans, outcomes)
        losses = [record["losses"][name] for name in baseline.experts]
        if record["evaluated"]:
            if self.evaluated_days >= TUNING_WARMUP_DAYS:
                for i, choice in enumerate(choices):
                    self.losses[i] += record["losses"][choice]
            self.evaluated_days += 1
        for model in self.models[1:]:
            if record["evaluated"]:
                model._update(losses)
            model.last_day, model.days = baseline.last_day, baseline.days
            model.history_digest = baseline.history_digest
        return record["evaluated"]

    def checkpoint(self):
        return {"models": [m.checkpoint() for m in self.models], "losses": list(self.losses),
                "evaluated_days": self.evaluated_days}

    def resume(self, state):
        saved = state["models"]
        digest = saved[0]["history_digest"]
        restored = [FixedShareTicketExperts.restore(item, context=model.context, history_digest=digest)
                    for item, model in zip(saved, self.models, strict=True)]
        if any((m.objective, m.experts, m.learning_rate, m.share) !=
               (expected.objective, expected.experts, expected.learning_rate, expected.share)
               for m, expected in zip(restored, self.models, strict=True)):
            raise ValueError("Fixed Share checkpoint has incompatible competitors.")
        losses = state["losses"]
        days = state["evaluated_days"]
        if (type(days) is not int or not 0 <= days <= restored[0].days
                or len(losses) != len(restored) or not all(isfinite(x) and 0 <= x <= days for x in losses)):
            raise ValueError("Invalid Fixed Share calibration state.")
        self.models, self.losses, self.evaluated_days = restored, list(losses), days

    def calibration(self):
        tested = max(0, self.evaluated_days - TUNING_WARMUP_DAYS)
        return {"warmup_days": TUNING_WARMUP_DAYS, "evaluated_days": tested,
                "trials": [{"learning_rate": model.learning_rate, "share": model.share, "sum_daily_loss": loss}
                           for model, loss in zip(self.models, self.losses, strict=True)],
                "learning_rate": self.selected.learning_rate, "share": self.selected.share}


def replay_ticket_experts(days, forecast, model, *, training=()):
    """Replay complete dates, giving the forecaster an immutable prior prefix."""
    past = list(training)
    for day, draws in days:
        draws = tuple((tuple(numbers), joker) for numbers, joker in draws)
        plans = forecast(tuple(past))
        record = model.observe(day, plans, draws)
        past.append((day, draws))
        yield record


# Only this process reuses forecasts. The effective history, complete layout,
# policy revision and every scoring option are part of the key.
_FORECAST_CACHE = OrderedDict()
_CACHE_LOCK = Lock()
_CACHE_SIZE = 16


@lru_cache(maxsize=256)
def _expert_scores(maximum, draw_n, payload, relative_bounds, formula_signature):
    from loto_enterprise.benchmark.methods import call_method

    draws = np.frombuffer(payload, dtype=np.int32).reshape(-1, draw_n)
    return {
        expert: call_method(expert, draws, maximum, history_cutoffs=relative_bounds)[0]
        for expert in DEFAULT_EXPERTS
    }


def _fixed_share_history(lot, frame):
    from loto_enterprise.core.draw_validation import valid_draw_matrix
    from loto_enterprise.core.history import chronological_history, history_dates

    frame = chronological_history(frame)
    dates = history_dates(frame)
    if dates is None:
        raise ValueError("Fixed Share cere data fiecărei extrageri.")
    matrix, valid = valid_draw_matrix(
        frame, [f"n{n}" for n in range(1, lot.draw_n + 1)],
        draw_n=lot.draw_n, max_num=lot.max_n,
    )
    if not len(matrix):
        raise ValueError("Fixed Share cere cel puțin o extragere principală validă.")
    dates = dates.iloc[np.flatnonzero(valid)].dt.strftime("%Y-%m-%d").tolist()
    bonus = np.zeros(len(matrix), dtype=np.int32)
    if lot.geo.second and "joker" in frame:
        valid_frame = frame.iloc[np.flatnonzero(valid)]
        values, valid_bonus = valid_draw_matrix(valid_frame, ["joker"], draw_n=1, max_num=lot.geo.second.max_n)
        bonus[valid_bonus] = values[:, 0]
    return dates, matrix.astype(np.int32), bonus


def _candidate_plans(lot, plan, scheme, objective, scores, bonus_score, eligible, joker_size, reference=None):
    from covering.lr_pool_budget import restrict_to_pool
    from covering.lr_pool_ranking import lr_pool_capacity, rank_lr_pool
    from loto_enterprise.core.ranking import rank_by_score

    blocked = set(reference["pool"]) if reference else set()
    left = eligible - blocked
    capacity = lr_pool_capacity(left)
    if capacity < scheme.n:
        raise ValueError(
            f"Fixed Share: dintre {len(left)} numere eligibile se pot alege cel mult {capacity} "
            f"fără trei consecutive. Codul {scheme.code} cere {scheme.n}."
        )
    excluded_jokers = set(reference["jokers"]) if reference else set()
    jokers = rank_by_score({n: value for n, value in bonus_score.items() if n not in excluded_jokers}, joker_size)
    if lot.geo.second and len(jokers) != joker_size:
        raise ValueError("Fixed Share: Urna 2 Joker nu are bile eligibile suficiente.")
    candidates = {}
    for method, values in scores.items():
        pool = sorted(rank_lr_pool({n: values.get(n, 0.0) for n in left}, scheme.n))
        played = restrict_to_pool(deepcopy(plan), objective, pool, jokers)
        if not played["tickets"]:
            raise ValueError("Bugetul nu produce bilete în pool-ul selectat de Fixed Share.")
        played["forecast"] = {
            "pool": pool, "jokers": jokers, "eligible_count": len(left),
            "excluded_pool": sorted(blocked), "excluded_jokers": sorted(excluded_jokers),
            "eligible_joker_count": lot.geo.second.max_n - len(excluded_jokers) if lot.geo.second else 0,
        }
        candidates[method] = played
    return candidates


def online_ticket_forecast(
    lot, plan, scheme, frame_before_day, *, inverse, number_range, lookback,
    joker_size, objective, cancelled=None,
):
    """Replay portfolio losses on prior days, then build the actually selected plan.

    Both the live recommendation and walk-forward call this exact function.
    All experts share one independent frequency ranking for Joker's second urn.
    In inverse mode all experts exclude the direct Fixed Share winner's pools
    from the same prefix, including its actual Urn 2 ranking.
    """
    from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN, lr_pool_capacity
    from loto_enterprise.benchmark.methods import call_method

    if cancelled is not None:
        cancelled()
    if not isfinite(lookback := float(lookback)) or not 0 <= lookback <= 100:
        raise ValueError("Procentul de istoric pentru Fixed Share trebuie să fie între 0 și 100.")
    dates, matrix, bonus = _fixed_share_history(lot, frame_before_day)
    maximum = lot.geo.second.max_n if lot.geo.second else 0
    if maximum:
        available = maximum // (2 if inverse else 1)
        if type(joker_size) is not int or not 1 <= joker_size <= available:
            raise ValueError(f"Fixed Share: Urna 2 Joker permite între 1 și {available} bile în acest mod.")
        if not bonus.any():
            raise ValueError("Fixed Share: istoricul Urnei 2 Joker nu conține extrageri valide.")
    else:
        joker_size = 0
    history_hash = sha256(
        "".join(dates).encode("ascii") + matrix.tobytes() + bonus.tobytes()
    ).hexdigest()
    from loto_enterprise.benchmark.lr_provenance import lr_semantics
    from loto_enterprise.core.cache_io import pickle_load_path, pickle_store_path_atomic
    from runtime_paths import WF_CACHE_DIR

    semantics = lr_semantics(lot, scheme, 4, layout=plan)
    context = fixed_share_context({
        "plan": plan, "scheme": scheme.code, "range": number_range, "lookback": lookback,
        "objective": objective, "joker_size": joker_size, "max_consecutive_run": LR_MAX_CONSECUTIVE_RUN,
        "training_days": TRAINING_DAYS, "experts": DEFAULT_EXPERTS, "semantics": semantics,
        "tuning": TUNING_GRID, "warmup": TUNING_WARMUP_DAYS,
    })
    key = (history_hash, context, bool(inverse))
    with _CACHE_LOCK:
        cached = _FORECAST_CACHE.get(key)
        if cached is not None:
            _FORECAST_CACHE.move_to_end(key)
            return deepcopy(cached)

    lo, hi = number_range or (1, lot.max_n)
    eligible = set(range(max(1, lo), min(hi, lot.max_n) + 1))
    if lr_pool_capacity(eligible) < scheme.n:
        raise ValueError(f"Fixed Share: baza aleasă nu permite codul LR {scheme.code} fără trei consecutive.")
    if inverse and len(eligible) < 2 * scheme.n:
        raise ValueError(
            f"Fixed Share invers: cele {len(eligible)} numere eligibile nu permit "
            f"două pool-uri disjuncte cu câte {scheme.n} numere."
        )
    direct = _TunedTicketExperts(DEFAULT_EXPERTS, objective, context)
    selected = _TunedTicketExperts(DEFAULT_EXPERTS, objective, context) if inverse else direct
    date_values = np.asarray(dates)
    bounds = np.searchsorted(date_values, date_values, side="left")
    starts = [0, *(np.flatnonzero(np.diff(bounds)) + 1).tolist(), len(matrix)]
    observed = 0
    skipped = 0

    def candidates(origin):
        width = origin if lookback in (0, 100) else max(1, int(origin * lookback / 100))
        start = origin - width
        cuts = tuple(map(int, np.maximum(bounds[start:origin] - start, 0)))
        values = _expert_scores(lot.max_n, lot.draw_n, matrix[start:origin].tobytes(), cuts, semantics["formulas"])
        bonus_score = {}
        if maximum:
            bonus_prefix = bonus[start:origin]
            if not bonus_prefix.any():
                return values, None
            bonus_score, _ = call_method("frequency", bonus_prefix[bonus_prefix != 0, None], maximum)
        return values, bonus_score

    recent = starts[max(1, len(starts) - TRAINING_DAYS - 1):]
    spans = list(zip(recent[:-1], recent[1:], strict=True))
    completed = 0
    mode = "inverse" if inverse else "direct"
    cache_path = WF_CACHE_DIR / "lr_ticket_experts" / f"{lot.bench_key}-{mode}.pkl"
    # One bounded slot per game/mode. A concurrent different forecast may replace
    # the file, but its context and history can never be adopted by this request.
    try:
        state = pickle_load_path(cache_path)
        if state["identity"] == key and fixed_share_context(state["progress"]) == state["checksum"]:
            progress = state["progress"]
            offset = progress["completed"]
            if type(offset) is not int or not 0 <= offset <= len(spans):
                raise ValueError("Invalid Fixed Share checkpoint origin.")
            direct.resume(progress["direct"])
            if inverse:
                selected.resume(progress["inverse"])
            completed, observed, skipped = offset, progress["observed"], progress["skipped"]
    except FileNotFoundError:
        pass
    except (OSError, EOFError, ZstdError, pickle.UnpicklingError, TypeError, KeyError, IndexError, ValueError) as exc:
        logging.warning("Fixed Share: recalculare după checkpoint ilizibil: %s", exc)
        direct = _TunedTicketExperts(DEFAULT_EXPERTS, objective, context)
        selected = _TunedTicketExperts(DEFAULT_EXPERTS, objective, context) if inverse else direct

    for step, (origin, end) in enumerate(spans[completed:], start=completed + 1):
        if cancelled is not None:
            cancelled()
        outcomes = [(list(map(int, matrix[row])), int(bonus[row]) if bonus[row] else None)
                    for row in range(origin, end)]
        scores, bonus_score = candidates(origin)
        counted = 0
        if bonus_score is not None:
            active = _candidate_plans(lot, plan, scheme, objective, scores, bonus_score, eligible, joker_size)
            if inverse:
                reference = active[direct.selected.choose()]["forecast"]
                if lr_pool_capacity(eligible.difference(reference["pool"])) >= scheme.n:
                    inverses = _candidate_plans(lot, plan, scheme, objective, scores, bonus_score,
                                                eligible, joker_size, reference)
                    counted = selected.observe(dates[origin], inverses, outcomes)
                direct.observe(dates[origin], active, outcomes)
            else:
                counted = selected.observe(dates[origin], active, outcomes)
        observed += counted
        skipped += not counted
        if step % TUNING_WARMUP_DAYS == 0 or step == len(spans):
            progress = {"completed": step, "observed": observed, "skipped": skipped,
                        "direct": direct.checkpoint(), "inverse": selected.checkpoint() if inverse else None}
            try:
                pickle_store_path_atomic(cache_path, {"identity": key, "checksum": fixed_share_context(progress),
                                                     "progress": progress})
            except OSError as exc:
                logging.warning("Fixed Share: checkpoint indisponibil: %s", exc)

    if cancelled is not None:
        cancelled()
    scores, bonus_score = candidates(len(matrix))
    if bonus_score is None:
        raise ValueError("Fixed Share: fereastra de istoric nu conține bile Joker valide.")
    active = _candidate_plans(lot, plan, scheme, objective, scores, bonus_score, eligible, joker_size)
    if inverse:
        reference = active[direct.selected.choose()]["forecast"]
        active = _candidate_plans(lot, plan, scheme, objective, scores, bonus_score, eligible, joker_size, reference)
    expert = selected.selected.choose()
    played = active[expert]
    played["forecast"].update(
        selection_policy=ONLINE_TICKET_EXPERTS,
        selection={
            "method": expert, "source": ONLINE_TICKET_EXPERTS, "version": FIXED_SHARE_VERSION,
            "calibration": selected.calibration(),
            "training_days": selected.evaluated_days, "training_draws": observed, "skipped_days": skipped,
            "available_days": len(spans), "available_draws": sum(end - origin for origin, end in spans),
            "unscored_draws": sum(end - origin for origin, end in spans) - observed,
            "weights": dict(zip(selected.selected.experts, selected.selected.weights, strict=True)),
        },
        joker_selection={"method": "frequency", "source": ONLINE_TICKET_EXPERTS} if maximum else None,
        history_hash=history_hash, rows=len(matrix),
        date=date.fromisoformat(dates[-1]).strftime("%d-%m-%Y"),
        generated=False, dynamic=True, code=scheme.code, inverse=inverse, number_range=number_range,
        inverse_range=number_range if inverse else None, max_consecutive_run=LR_MAX_CONSECUTIVE_RUN,
    )
    with _CACHE_LOCK:
        _FORECAST_CACHE[key] = deepcopy(played)
        _FORECAST_CACHE.move_to_end(key)
        if len(_FORECAST_CACHE) > _CACHE_SIZE:
            _FORECAST_CACHE.popitem(last=False)
    return played
