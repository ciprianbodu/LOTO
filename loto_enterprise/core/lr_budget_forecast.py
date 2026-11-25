"""Fill budget tickets from a pool ranked by the production selector."""

from __future__ import annotations

from covering.lr_catalog_cache import catalog_cache
import io
import logging
import math
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_budget import recommend_games
from covering.lr_pool_budget import required_pool_size, restrict_to_pool
from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN, lr_pool_capacity, rank_lr_pool
from covering.lr_selection import lr_pool_sizes, resolve_lr_scheme, select_lr_scheme
from covering.probability import _integer
from loto_engine import LotoEngine
from loto_enterprise.benchmark import decision
from loto_enterprise.benchmark.lr_provenance import dynamic_formulas, lr_semantics
from loto_enterprise.benchmark.methods import call_method, list_methods
from loto_enterprise.benchmark.restricted_decision import restricted_bench_scores
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.dynamic_winner import choose_dynamic_winner
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS, online_ticket_forecast
from loto_enterprise.core.method_selector import combine_ensemble_scores
from loto_enterprise.core.ranking import longest_consecutive_run, rank_by_score


_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def budget_plan_scheme(plan):
    """Official scoring scheme for the full set of positions the budget needs."""
    game = plan["game"]
    zones = [zone for ticket in plan["tickets"] for zone in ticket]
    if not zones:
        raise ValueError("Bugetul nu permite nicio schemă LR pentru acest joc.")
    if len(zones) == 1:
        return resolve_lr_scheme(game, zones[0]["code"], pool_size=zones[0]["pool_size"])
    required = required_pool_size(plan)
    sizes = lr_pool_sizes(game)
    size = next((width for width in sizes if width >= required), sizes[-1])
    return select_lr_scheme(game, size)


@catalog_cache(maxsize=32, semantic_key=dynamic_formulas)
def _dynamic_scores(game, code, draws, cutoffs, excluded_numbers=()):
    lot = require_lottery(game, "RO")
    matrix = np.frombuffer(draws, dtype=np.int32).reshape(-1, lot.draw_n)
    return choose_dynamic_winner(
        matrix, cutoffs, resolve_lr_scheme(game, code), excluded_numbers=excluded_numbers,
        max_run=LR_MAX_CONSECUTIVE_RUN,
    )


def _forecast_engine(lot, source, lookback, expected_hash=None):
    engine = LotoEngine(lot.geometry, game_key=lot.bench_key, country=lot.country)
    if source is None:
        loaded = engine.load_data(str(_ROOT / lot.csv))
    else:
        # Replay the worker's index-free CSV serialization, including dtype inference,
        # chronological normalization and the content hash of the complete valid history.
        with io.StringIO(source[1].to_csv(index=False)) as stream:
            loaded = engine.load_data(stream)
    if not loaded:
        raise ValueError("Istoricul jocului nu conține extrageri valide.")
    if expected_hash and engine.audit["hash"] != expected_hash:
        raise ValueError("Istoricul disponibil diferă de cel al pool-ului generat. Regenerați pool-ul.")
    engine._bind_lr_decision()
    return _apply_lookback(engine, lookback)


def _apply_lookback(engine, lookback):
    if 0 < lookback < 100:
        rows = max(1, int(len(engine.data) * (lookback / 100)))
        engine.data = engine.data.tail(rows)
        engine._draw_matrix = engine._draw_matrix[-rows:]
    return engine


def _stored_members_valid(members, methods):
    return isinstance(members, list) and bool(members) and all(
        isinstance(member, dict) and isinstance(member.get("method"), str) and member["method"] in methods
        and type(member.get("weight")) in (int, float) and math.isfinite(member["weight"]) and member["weight"] > 0
        for member in members
    )


def _rebench_candidates(entries, lot, scheme, number_range, dynamic, lookback, hit_target, joker_size, layout):
    """Keep complete, reusable profiles for this geometry before validating the full CSV hash."""
    if not entries or number_range not in (None, (10, 40)):
        return []
    urn_keys = {lot.bench_key}
    if lot.bench_key_urna2:
        urn_keys.add(lot.bench_key_urna2)
    methods = set(list_methods())
    candidates = []
    target = decision.BENCH_HIT_TARGET if hit_target is None else hit_target
    semantics = lr_semantics(lot, scheme, target, layout=layout)
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("game") != lot.game_id:
            continue
        settings, report = entry.get("settings"), entry.get("result")
        if (not isinstance(settings, dict) or not isinstance(report, dict) or not report.get("history_hash")
                or report.get("max_consecutive_run") != LR_MAX_CONSECUTIVE_RUN
                or report.get("semantics") != semantics):
            continue
        if (settings.get("restricted") is not (number_range is not None)
                or settings.get("dynamic") is not dynamic or type(settings.get("inverse")) is not bool
                or str(settings.get("scheme")) != str(scheme.code)
                or type(settings.get("lookback")) not in (int, float)
                or (settings["lookback"] or 100) != (lookback or 100)
                or settings.get("hit_target") != (decision.BENCH_HIT_TARGET if hit_target is None else hit_target)
                or (lot.bench_key_urna2 and settings.get("joker_size") != joker_size)):
            continue
        urns = {}
        for urn in report.get("urns") or ():
            if not isinstance(urn, dict) or urn.get("game_key") not in urn_keys:
                continue
            profile = urn.get("decision")
            if isinstance(profile, dict) and _stored_members_valid(profile.get("members"), methods):
                urns[urn["game_key"]] = profile
        if urns.keys() == urn_keys:
            candidates.append((report["history_hash"], settings["inverse"], urns))
    return candidates


def _profile_scores(choice, matrix, cutoffs, maximum, excluded, count):
    profile, member_key = choice
    contributions = []
    blocked = set(excluded)
    for member in profile[member_key]:
        method = member["method"]
        scores, _ = call_method(method, matrix, maximum, history_cutoffs=cutoffs)
        eligible = {n: score for n, score in scores.items() if 1 <= n <= maximum and n not in blocked}
        contributions.append((method, eligible, member["weight"]))
    audit = {}
    scores = combine_ensemble_scores(contributions, audit)
    if len(scores) < count:
        raise ValueError("Metoda salvată de Re-Bench nu a produs suficiente scoruri pentru acest pool.")
    active = audit["ensemble_active"]
    reference = member_key == "reference_members"
    return scores, {
        "method": " + ".join(name for name, _ in active),
        "members": [{"method": name, "weight": weight} for name, weight in active],
        "source": "lr_rebench_reference" if reference else "lr_rebench",
        "fallback": bool(profile.get("fallback")) and not reference,
        "hit_target": profile.get("target"), "evaluated": profile.get("evaluated"), "folds": profile.get("folds", []),
    }


def _same_profile_recipe(actual, expected):
    return isinstance(expected, list) and bool(expected) and len(actual) == len(expected) and all(
        isinstance(reference, dict) and member["method"] == reference.get("method")
        and type(reference.get("weight")) in (int, float) and member["weight"] == reference["weight"]
        for member, reference in zip(actual, expected, strict=True)
    )


def _mismatched_inverse_references(lot, engine, scheme, dynamic, profile, direct_profiles, direct_forecast):
    """Ensure a paid inverse profile excludes the reference policy it evaluated."""
    mismatched = []
    for key, (inverse, _) in profile.items():
        expected = inverse.get("reference_members")
        current = direct_profiles.get(key)
        if current is not None:
            actual, member_key = current
            if not _same_profile_recipe(actual[member_key], expected):
                mismatched.append(key)
        elif key == lot.bench_key and dynamic and expected in ([], ()):
            if direct_forecast["generated"]:
                scores, _ = _dynamic_scores(
                    lot.game_id, scheme.code, engine._draw_matrix.tobytes(), training_cutoffs(engine.data), (),
                )
                if set(rank_lr_pool(scores, scheme.n)) != set(direct_forecast["pool"]):
                    mismatched.append(key)
        else:
            mismatched.append(key)
    return sorted(mismatched)


def _check_capacity(name, maximum, count, excluded):
    remaining = maximum - len(excluded)
    if remaining < count:
        raise ValueError(
            f"{name}: rămân {remaining} numere eligibile după excluderea celor "
            f"{len(excluded)} din pool-ul normal. Sunt necesare {count}."
        )


def _score_pool(lot, engine, scheme, dynamic, joker_size, *, excluded=(), excluded_jokers=(), hit_target=None,
                profiles=None, bonus_history=None):
    eligible = set(range(1, lot.max_n + 1)).difference(excluded)
    capacity = lr_pool_capacity(eligible)
    if capacity < scheme.n:
        raise ValueError(
            f"Din {len(eligible)} numere eligibile se pot alege cel mult {capacity} fără trei consecutive. "
            f"Cod {scheme.code} necesită {scheme.n}."
        )
    engine._winner_pool_hint = scheme.n
    profiles = profiles or {}
    if lot.bench_key in profiles:
        scores, selection = _profile_scores(
            profiles[lot.bench_key], engine._draw_matrix, training_cutoffs(engine.data), lot.max_n, excluded, scheme.n,
        )
    elif dynamic:
        scores, selection = _dynamic_scores(
            lot.game_id, scheme.code, engine._draw_matrix.tobytes(), training_cutoffs(engine.data), excluded,
        )
    elif excluded:
        scores, selection = restricted_bench_scores(
            engine._draw_matrix, training_cutoffs(engine.data), lot.bench_key, scheme.n, excluded, hit_target,
            strict_lr=True,
        )
    else:
        scores = engine._get_timesfm_scores()
        selection = engine.audit.get("bench_winner", {}).get(
            lot.bench_key, {"method": "frequency", "fallback": True},
        )
    if not scores:
        scores, _ = call_method("frequency", engine._draw_matrix, lot.max_n)
    pool = rank_lr_pool({int(n): s for n, s in scores.items() if int(n) in eligible}, scheme.n)
    jokers, bonus = (), None
    if lot.bench_key_urna2:
        maximum = lot.geo.second.max_n
        if lot.bench_key_urna2 in profiles or excluded_jokers:
            history = bonus_history if bonus_history is not None and lot.bench_key_urna2 in profiles else engine.data
            matrix, valid = valid_draw_matrix(history, ["joker"], draw_n=1, max_num=maximum)
            if not len(matrix):
                raise ValueError("Istoricul Urnei 2 Joker nu conține extrageri valide.")
            bounds = training_cutoffs(history.loc[valid])
            if lot.bench_key_urna2 in profiles:
                bonus_scores, bonus = _profile_scores(
                    profiles[lot.bench_key_urna2], matrix, bounds, maximum, excluded_jokers, joker_size,
                )
            else:
                bonus_scores, bonus = restricted_bench_scores(
                    matrix, bounds, lot.bench_key_urna2, 1, excluded_jokers, hit_target,
                )
            jokers = tuple(rank_by_score({
                n: score for n, score in bonus_scores.items() if 1 <= n <= maximum and n not in excluded_jokers
            }, joker_size))
            if len(jokers) != joker_size:
                raise ValueError("Pool-ul eligibil al Urnei 2 Joker nu poate fi completat.")
        elif engine._valid_joker_values().size:
            bonus_scores = engine._get_timesfm_scores(is_joker_drum=True)
            ranked = rank_by_score({n: s for n, s in bonus_scores.items() if 1 <= n <= maximum}, joker_size)
            if not ranked:
                ranked = engine._get_hard_core_joker(engine.analyze_joker_frequency(), pool_size=joker_size)
            jokers = tuple(ranked)
            bonus = engine.audit.get("bench_winner", {}).get(
                lot.bench_key_urna2, {"method": "frequency", "fallback": True},
            )
    dates = history_dates(engine.data)
    return {
        "pool": pool, "jokers": jokers, "selection": deepcopy(selection), "joker_selection": bonus,
        "date": dates.iloc[-1].strftime("%d-%m-%Y") if dates is not None else None,
        "rows": len(engine.data), "history_hash": engine.audit.get("hash"),
        "history_sha256": engine.audit.get("history_sha256"),
        "code": scheme.code, "dynamic": dynamic, "generated": False,
    }


def _generated_pool(lot, result):
    audit = result.get("audit") or {}
    winners = audit.get("bench_winner") or {}
    main = winners.get(lot.bench_key) or {}
    selection = audit.get("dynamic_selection") or main.get("dynamic_selection")
    context = result.get("context") or {}
    days = context.get("last_3") or []
    date = days[-1].get("date") if days else None
    return {
        "pool": result.get("hard_core"),
        "jokers": result.get("joker_pool") or result.get("hard_core_joker") or (),
        "selection": selection or main,
        "joker_selection": winners.get(lot.bench_key_urna2),
        "date": date if date and date != "N/A" else None,
        "rows": result.get("total_draws") or 0,
        "history_hash": audit.get("hash"),
        "history_sha256": audit.get("history_sha256"),
        "code": (result.get("lr_scheme_code") or (audit.get("lr_scheme") or {}).get("code")
                 or context.get("lr_scheme_code") or (selection or {}).get("scheme_code")),
        "dynamic": bool(selection), "generated": True,
    }


def _balls(values, maximum):
    balls = tuple(_integer(n) for n in (values or ()))
    if len(set(balls)) != len(balls) or any(not 1 <= n <= maximum for n in balls):
        raise ValueError("Pool-ul conține numere duplicate sau în afara urnei.")
    return balls


def _number_range(limits):
    if limits is None:
        return None
    if not isinstance(limits, (list, tuple)) or len(limits) != 2:
        raise ValueError("Intervalul numerelor principale necesită două limite întregi.")
    low, high = map(_integer, limits)
    if low > high:
        raise ValueError("Limita inferioară a intervalului depășește limita superioară.")
    return low, high


def _range_exclusions(lot, scheme, number_range, normal_pool=()):
    """Fixed main-urn exclusions shared by historical ranking and the next draw."""
    blocked = set(normal_pool)
    if number_range is None:
        _check_capacity(lot.name, lot.max_n, scheme.n, blocked)
    else:
        low, high = number_range
        blocked.update(n for n in range(1, lot.max_n + 1) if not low <= n <= high)
        eligible = lot.max_n - len(blocked)
        if eligible < scheme.n:
            detail = " după excluderea pool-ului normal" if normal_pool else ""
            raise ValueError(
                f"În intervalul {low}–{high} rămân {eligible} numere eligibile{detail}. "
                f"Cod {scheme.code} necesită {scheme.n}."
            )
    return tuple(sorted(blocked))


def _inverse_forecast(lot, engine, scheme, dynamic, normal_pool, normal_jokers, hit_target, number_range,
                      profiles=None, bonus_history=None):
    normal = tuple(sorted(normal_pool))
    blocked = _range_exclusions(lot, scheme, number_range, normal)
    excluded_jokers = tuple(sorted(normal_jokers))
    if lot.bench_key_urna2:
        _check_capacity("Urna 2 Joker", lot.geo.second.max_n, len(normal_jokers), excluded_jokers)
    forecast = _score_pool(
        lot, engine, scheme, dynamic, len(normal_jokers), excluded=blocked,
        excluded_jokers=excluded_jokers, hit_target=hit_target, profiles=profiles, bonus_history=bonus_history,
    )
    forecast.update(excluded_pool=list(normal), eligible_count=lot.max_n - len(blocked), inverse_range=number_range)
    if lot.bench_key_urna2:
        forecast.update(excluded_jokers=excluded_jokers, eligible_joker_count=lot.geo.second.max_n - len(excluded_jokers))
    return forecast


def _budget_forecast(plan, lot, requested_code, result, source, lookback, dynamic, joker_size, inverse, hit_target,
                     number_range, exclude_generated_pool=False, lr_rebench_entries=(), selection_policy=None):
    scheme = budget_plan_scheme(plan)
    number_range = _number_range(number_range)
    filename = source[0] if source else Path(lot.csv).name
    if selection_policy == ONLINE_TICKET_EXPERTS:
        if exclude_generated_pool:
            raise ValueError("Fixed Share necesită pool-ul direct calculat cu aceeași politică.")
        engine = _forecast_engine(lot, source, 0)
        played = online_ticket_forecast(
            lot, plan, scheme, engine.data, inverse=inverse, number_range=number_range,
            lookback=lookback, joker_size=joker_size, objective=plan["objective"],
        )
        plan.update(played)
        forecast = plan["forecast"]
        forecast.update(filename=filename, history_hash=engine.audit["hash"],
                        history_sha256=engine.audit["history_sha256"])
        return forecast
    engine, bonus_history = None, None
    profiles = {}
    candidates = _rebench_candidates(
        () if exclude_generated_pool else lr_rebench_entries, lot, scheme, number_range,
        dynamic, lookback, hit_target, joker_size, plan,
    )
    if candidates:
        try:
            current = _forecast_engine(lot, source, 0)
        except (ValueError, OSError):
            # A completed job may still supply a playable direct pool without a local CSV.
            current = None
        if current is not None:
            for fingerprint, mode, urns in candidates:
                if fingerprint == current.audit["hash"]:
                    profiles.setdefault(mode, urns)
            if profiles:
                if lot.bench_key_urna2:
                    _, valid = valid_draw_matrix(current.data, ["joker"], draw_n=1, max_num=lot.geo.second.max_n)
                    bonus_history = current.data.loc[valid]
                    if 0 < lookback < 100:
                        bonus_history = bonus_history.tail(max(1, int(len(bonus_history) * (lookback / 100))))
                engine = _apply_lookback(current, lookback)
    inverse_profiles = {key: (p, "members") for key, p in profiles.get(True, {}).items()}
    direct_profiles = {key: (p, "members") for key, p in profiles.get(False, {}).items()}
    if not direct_profiles and inverse_profiles:
        methods = set(list_methods())
        direct_profiles = {
            key: (p, "reference_members") for key, (p, _) in inverse_profiles.items()
            if _stored_members_valid(p.get("reference_members"), methods)
        }
    if profiles and result:
        # An inverse winner ranks the complement. Preserve an identical direct job when
        # this benchmark supplied no replacement recipe for the direct pool.
        generated_hash = (result[1].get("audit") or {}).get("hash")
        if direct_profiles or filename != result[0] or generated_hash != engine.audit["hash"]:
            result = None
    saved = result[1] if result else {}
    previous = _generated_pool(lot, saved) if result else None
    saved_lookback = saved.get("lookback", (saved.get("audit") or {}).get("lookback_pct"))
    same_lookback = saved_lookback is None or (float(saved_lookback) or 100) == (lookback or 100)
    if previous:
        size = len(previous["pool"] or ())
        code = previous["code"] or requested_code
        saved_mode = saved.get("dynamic_method", previous["dynamic"] if previous["selection"] else dynamic)
        same_settings = (
            same_lookback and saved_mode == dynamic
            and (not lot.bench_key_urna2 or len(previous["jokers"]) == joker_size)
        )
    else:
        code = requested_code
        size = resolve_lr_scheme(lot.geometry, code).n if code is not None else scheme.n
        same_settings = False

    # A dynamic winner is specific to the exact LR layout, even at equal width.
    same_scheme = str(code) == str(scheme.code) or (code is None and not dynamic)
    if exclude_generated_pool and (not inverse or previous is None or not size):
        raise ValueError("Excluderea pool-ului generat necesită un rezultat normal și modul invers.")
    # An email describes a completed job: its displayed original is the exclusion
    # even when the budget selects a different scoring scheme for the alternative.
    reuse = exclude_generated_pool or (same_settings and size == scheme.n and same_scheme)
    # A legacy dynamic winner may have been chosen on pools that allowed triples.
    saved_limit = saved.get("max_consecutive_run")
    if saved_limit is None:
        saved_limit = (saved.get("context") or {}).get("max_consecutive_run")
    if isinstance(saved_limit, dict):
        saved_limit = saved_limit.get("requested")
    reuse_main = exclude_generated_pool or (
        reuse and (not dynamic or saved_limit == LR_MAX_CONSECUTIVE_RUN)
        and longest_consecutive_run(_balls(previous["pool"], lot.max_n)) <= LR_MAX_CONSECUTIVE_RUN
    )
    restricted_direct = number_range is not None and not exclude_generated_pool
    if engine is None and (not reuse_main or inverse or restricted_direct):
        if result and filename != result[0]:
            raise ValueError("Lipsește istoricul folosit la generarea acestui pool.")
        expected_hash = (saved.get("audit") or {}).get("hash")
        engine = _forecast_engine(lot, source, lookback, expected_hash)
        if previous and not expected_hash:
            dates = history_dates(engine.data)
            if same_lookback and previous["rows"] and previous["rows"] != len(engine.data):
                raise ValueError("Numărul de extrageri diferă de istoricul pool-ului generat. Regenerați pool-ul.")
            if previous["date"]:
                date = history_dates(pd.DataFrame({"date": [previous["date"]]})).iloc[0]
                if dates is None or date != dates.iloc[-1]:
                    raise ValueError("Ultima extragere diferă de istoricul pool-ului generat. Regenerați pool-ul.")
            if source is None and not (previous["rows"] and previous["date"]):
                raise ValueError("Istoricul inițial al pool-ului generat este indisponibil. Regenerați pool-ul.")

    # The inverse uses the direct pool for the same interval. The email
    # explicitly pairs its alternative with an already generated normal pool.
    if reuse_main and not restricted_direct:
        forecast = previous
        filename = result[0]
    else:
        blocked = _range_exclusions(lot, scheme, number_range) if restricted_direct else ()
        forecast = _score_pool(lot, engine, scheme, dynamic, joker_size, excluded=blocked, hit_target=hit_target,
                               profiles=direct_profiles, bonus_history=bonus_history)
        if reuse and lot.bench_key_urna2:
            # Changes to the main urn preserve the compatible generated Joker choice.
            forecast.update(jokers=previous["jokers"], joker_selection=previous["joker_selection"])
        forecast["eligible_count"] = lot.max_n - len(blocked)

    pool = _balls(forecast["pool"], lot.max_n)
    jokers = _balls(forecast["jokers"], lot.geo.second.max_n) if lot.bench_key_urna2 else ()
    if lot.bench_key_urna2 and not jokers:
        raise ValueError("Pool-ul Urnei 2 Joker este indisponibil.")
    if inverse:
        mismatched = _mismatched_inverse_references(
            lot, engine, scheme, dynamic, inverse_profiles, direct_profiles, forecast,
        )
        forecast = _inverse_forecast(lot, engine, scheme, dynamic, pool, jokers, hit_target, number_range,
                                     profiles=None if mismatched else inverse_profiles, bonus_history=bonus_history)
        if mismatched:
            forecast["profile_reference_mismatch"] = mismatched
    else:
        forecast.update(pool=pool, jokers=jokers)
        forecast.setdefault("eligible_count", lot.max_n)
    forecast["history_hash"] = engine.audit.get("hash") if engine is not None else (saved.get("audit") or {}).get("hash")
    forecast["number_range"] = number_range
    forecast["max_consecutive_run"] = LR_MAX_CONSECUTIVE_RUN
    if size != scheme.n:
        forecast.update(expanded_from=size, expanded_to_max=required_pool_size(plan) > scheme.n)
    forecast.update(filename=filename, code=scheme.code, pool=sorted(forecast["pool"]), inverse=inverse)
    return forecast


def playable_plan_order(item):
    """Order playable tickets by exact probability, price and game identifier."""
    return (
        not item["tickets"], -Fraction(item["attained"], item["denominator"]),
        item["price_bani"], item["game"],
    )


def recommend_playable_games(
    budget_lei, objective, game_id=None, histories=None, *,
    pool_codes, generated=None, lookback=100, dynamic=True, joker_size=5, inverse=False, hit_target=None,
    inverse_range=None, number_range=None, exclude_generated_pool=False, lr_rebench_entries=(),
    selection_policy=None,
):
    """Build a budget plan under the chosen pool or paid-portfolio policy."""
    if selection_policy not in (None, ONLINE_TICKET_EXPERTS):
        raise ValueError(f"Politică de selecție LR necunoscută: {selection_policy!r}.")
    limits = number_range if number_range is not None or not inverse else inverse_range
    lr_rebench_entries = tuple(lr_rebench_entries or ())
    plans = recommend_games(budget_lei, objective, game_id)
    for plan in plans:
        lot = require_lottery(plan["game"], "RO")
        result = (generated or {}).get(lot.game_id)
        source = (histories or {}).get(lot.game_id)
        filename = result[0] if result else (source[0] if source else Path(lot.csv).name)
        try:
            forecast = _budget_forecast(
                plan, lot, pool_codes.get(lot.game_id), result, source,
                lookback, dynamic, joker_size, inverse, hit_target, limits, exclude_generated_pool, lr_rebench_entries,
                **({"selection_policy": selection_policy} if selection_policy else {}),
            )
            if selection_policy is None and not restrict_to_pool(plan, objective, forecast["pool"], forecast["jokers"])["tickets"]:
                plan["forecast_error"] = f"Niciun cod LR în buget pentru pool-ul de {len(forecast['pool'])} numere."
            if plan["tickets"]:
                from loto_enterprise.core.lr_played_portfolio import evaluation_contract
                plan["forecast_pool"] = forecast["pool"]
                plan["evaluation"] = evaluation_contract(
                    plan, history_hash=forecast.get("history_hash"), inverse=inverse, number_range=limits,
                    dynamic=dynamic, lookback=lookback, hit_target=hit_target, policy=selection_policy)
            plan["forecast"] = forecast
        except (OSError, ValueError) as exc:
            logger.warning("Sugestie LR %s (%s): %s", lot.game_id, filename, exc)
            plan["forecast_error"] = f"{filename} · {exc}"
        except Exception:
            logger.exception("Sugestie LR pentru %s", lot.game_id)
            plan["forecast_error"] = f"{filename} · Calculul biletelor a eșuat."
        if "forecast" not in plan:
            plan.update(tickets=[], variants=0, price_bani=0, attained=0, upper=None, optimal=False)
    return sorted(plans, key=playable_plan_order)
