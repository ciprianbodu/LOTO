"""Reject malformed or incomplete external covering designs before any import."""

import hashlib
import json
from math import comb
from pathlib import Path

import pytest

from scripts.analysis import import_ljcr_designs as importer


def _section(rows=None, key="C(4,3,2)"):
    if rows is None:
        rows = [[3, 2, 1], [4, 2, 1], [4, 3, 1]]
    payload = json.dumps({key: rows}, separators=(",", ":"))[1:-1].encode("ascii")
    # Do not use the importer to produce its expected output.
    normalized = b"1 2 3\n1 2 4\n1 3 4\n"
    record = {
        "file": "C_4_3_2.txt",
        "v": 4,
        "pick": 3,
        "guarantee": 2,
        "blocks": len(rows),
        "coverage_subsets": comb(4, 2),
        "section_sha256": hashlib.sha256(payload).hexdigest(),
        "design_sha256": hashlib.sha256(normalized).hexdigest(),
    }
    return payload, record, normalized


def test_valid_source_is_normalized_only_within_blocks():
    payload, record, normalized = _section()
    assert importer.verify_section(payload, record) == normalized
    assert importer.verify_section(payload, record) == normalized


def test_changed_source_is_rejected_even_when_it_contains_a_valid_design():
    payload, record, _ = _section()
    with pytest.raises(ValueError, match="source SHA-256 mismatch"):
        importer.verify_section(payload + b" ", record)


def test_normalized_design_hash_is_independently_checked():
    payload, record, _ = _section()
    record["design_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="normalized SHA-256 mismatch"):
        importer.verify_section(payload, record)


@pytest.mark.parametrize(
    "bad_row",
    [[1, 2, 2], [1, 2, 5], [0, 2, 3], [True, 2, 3], [1.0, 2, 3], [1, 2], 123],
)
def test_malformed_blocks_fail_even_with_matching_source_hash(bad_row):
    payload, record, _ = _section([bad_row, [1, 2, 4], [1, 3, 4]])
    with pytest.raises(ValueError, match="invalid block"):
        importer.verify_section(payload, record)


def test_repeated_block_is_not_counted_as_additional_coverage():
    payload, record, _ = _section([[1, 2, 3], [1, 2, 4], [3, 2, 1]])
    with pytest.raises(ValueError, match="duplicate block"):
        importer.verify_section(payload, record)


def test_valid_blocks_without_full_guarantee_are_rejected():
    # Pair (3, 4) remains uncovered. Source hash and block count are correct.
    payload, record, _ = _section([[1, 2, 3], [1, 2, 4]])
    with pytest.raises(ValueError, match="incomplete guarantee"):
        importer.verify_section(payload, record)


def test_declared_count_and_coverage_must_match_actual_geometry():
    payload, record, _ = _section()
    with pytest.raises(ValueError, match="unexpected number of blocks"):
        importer.verify_section(payload, dict(record, blocks=4))
    with pytest.raises(ValueError, match="incomplete guarantee"):
        importer.verify_section(payload, dict(record, coverage_subsets=5))


@pytest.mark.parametrize(
    "changes",
    [{"v": True}, {"v": 17}, {"pick": 2}, {"pick": 5}, {"guarantee": 0}],
)
def test_unsupported_geometry_is_rejected(changes):
    payload, record, _ = _section()
    with pytest.raises(ValueError, match="Unsupported design geometry"):
        importer.verify_section(payload, dict(record, **changes))


def test_section_key_and_filename_must_match_geometry():
    payload, record, _ = _section(key="C(5,3,2)")
    with pytest.raises(ValueError, match="section key and geometry disagree"):
        importer.verify_section(payload, record)
    payload, record, _ = _section()
    with pytest.raises(ValueError, match="filename and geometry disagree"):
        importer.verify_section(payload, dict(record, file="../C_4_3_2.txt"))


def test_incomplete_json_is_rejected_even_with_matching_source_hash():
    payload, record, _ = _section()
    payload = payload[:-1]
    record["section_sha256"] = hashlib.sha256(payload).hexdigest()
    with pytest.raises(ValueError, match="invalid JSON section"):
        importer.verify_section(payload, record)


def test_conflicting_output_is_detected_before_writing_other_files(tmp_path):
    (tmp_path / "existing.txt").write_bytes(b"original")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        importer.write_verified_files(
            tmp_path, [("new.txt", b"new"), ("existing.txt", b"replacement")]
        )
    assert not (tmp_path / "new.txt").exists()
    assert (tmp_path / "existing.txt").read_bytes() == b"original"
    importer.write_verified_files(tmp_path, [("existing.txt", b"original")])
    assert (tmp_path / "existing.txt").read_bytes() == b"original"


class _Response:
    def __init__(self, status=206, content_range=None, payload=b"abc"):
        self.status = status
        self.headers = {
            "Content-Range": content_range or f"bytes 10-12/{importer.ARCHIVE_BYTES}"
        }
        self.payload = payload
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, size):
        self.read_sizes.append(size)
        return self.payload[:size]


@pytest.mark.parametrize(
    "response, message",
    [
        (_Response(status=200), "honor the pinned archive byte range"),
        (_Response(content_range="bytes 10-13/100"), "honor the pinned archive byte range"),
        (_Response(payload=b"ab"), "Unexpected archive section length"),
        (_Response(payload=b"abcd"), "Unexpected archive section length"),
    ],
)
def test_download_refuses_ignored_ranges_or_incomplete_content(monkeypatch, response, message):
    monkeypatch.setattr(importer.urllib.request, "urlopen", lambda *a, **k: response)
    with pytest.raises(ValueError, match=message):
        importer.download_section({"start": 10, "end": 12})


def test_download_is_bounded_to_the_requested_section(monkeypatch):
    response = _Response()
    calls = []

    def open_response(request, timeout):
        calls.append((request, timeout))
        return response

    monkeypatch.setattr(importer.urllib.request, "urlopen", open_response)
    assert importer.download_section({"start": 10, "end": 12}) == b"abc"
    request, timeout = calls[0]
    assert request.full_url == importer.ARCHIVE_URL
    assert request.get_header("Range") == "bytes=10-12"
    assert timeout == 60 and response.read_sizes == [4]


def test_pinned_manifest_matches_all_delivered_designs(tmp_path):
    manifest = json.loads(importer.MANIFEST.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parent
    records = manifest["records"]
    assert records and len({r["file"] for r in records}) == len(records)
    for record in records:
        raw = (root / "covering_designs" / record["file"]).read_text(encoding="ascii").encode("ascii")
        assert hashlib.sha256(raw).hexdigest() == record["design_sha256"]
        # Git autocrlf may change checkout bytes; the manifest pins canonical LF.
        replica = tmp_path / record["file"]
        replica.write_bytes(raw.replace(b"\n", b"\r\n"))
        portable = replica.read_text(encoding="ascii").encode("ascii")
        assert hashlib.sha256(portable).hexdigest() == record["design_sha256"]
        assert len(raw.decode("ascii").splitlines()) == record["blocks"]
        assert record["blocks"] < record["previous_blocks"]


@pytest.fixture
def refiner(monkeypatch):
    # The standalone script limits BLAS threads at import; keep that local to tests.
    import os

    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.setenv(key, os.environ.get(key, "1"))
    from scripts.analysis import refine_budget_designs

    return refine_budget_designs


def test_refiner_dominance_is_strict_componentwise_and_same_length(refiner):
    assert refiner._dominates((4, 6, 8), (4, 5, 8))
    assert not refiner._dominates((4, 5, 8), (4, 5, 8))
    assert not refiner._dominates((3, 6, 8), (4, 5, 8))
    assert not refiner._dominates((5,), (4, 9))


def _clustered_tickets():
    from itertools import combinations

    return [list(t) for t in list(combinations(range(1, 9), 6))[:7]]


def _complete_eight(refiner):
    root = Path(__file__).resolve().parent
    return refiner._read_complete(root / "covering_designs" / "C_8_6_4.txt", 8)


def test_refiner_integer_metrics_match_independent_subset_enumeration(refiner):
    from itertools import combinations

    tickets = _clustered_tickets()
    search = refiner.Search(8, {7: tickets})
    expected = []
    for universe in (45, 49):
        totals = [0, 0, 0, 0]
        for size in range(7):
            outside_ways = comb(universe - 8, 6 - size)
            for subset in combinations(range(1, 9), size):
                best = max(len(set(subset) & set(ticket)) for ticket in tickets)
                for j, threshold in enumerate((3, 4, 5, 6)):
                    if best >= threshold:
                        totals[j] += outside_ways
        expected.extend(totals)
    expected.append(
        sum(
            any(set(subset) <= set(ticket) for ticket in tickets)
            for subset in combinations(range(1, 9), 4)
        )
    )
    assert search.metrics(search.matrix(tickets)) == tuple(expected)


def test_refiner_accepts_real_gain_and_keeps_a_certificate_on_both_games(refiner):
    baseline = _clustered_tickets()
    candidate = _complete_eight(refiner)
    search = refiner.Search(8, {7: baseline})
    before = search.best_metrics[7]
    after = search.metrics(search.matrix(candidate))
    assert search.consider(candidate, after, "fixture")
    assert search.best[7] == candidate and search.best_metrics[7] == after
    assert after[1] > before[1] and after[5] > before[5]
    assert after[-1] == comb(8, 4)
    assert all(a >= b for a, b in zip(after, before))
    assert not search.consider(candidate, after, "no further gain")


def test_refiner_rejects_bad_candidates_despite_forged_better_metrics(refiner):
    baseline = _complete_eight(refiner)
    search = refiner.Search(8, {7: baseline})
    before = search.best_metrics[7]
    forged = tuple(x + 1 for x in before)
    weaker = _clustered_tickets()
    assert {n for t in weaker for n in t} == set(range(1, 9))
    assert not search.consider(weaker, forged, "incomplete cover")
    duplicate = baseline[:-1] + [baseline[0]]
    assert not search.consider(duplicate, forged, "duplicate")
    assert search.best[7] == baseline and search.best_metrics[7] == before
    assert search.origins == {}


def test_refiner_handles_sparse_budget_sections_deterministically(refiner):
    pool = list(range(1, 8))
    rows = [[n for n in pool if n != missing] for missing in pool]
    designs = {4: rows[:4], 6: rows[:6]}
    first, second = refiner.Search(7, designs), refiner.Search(7, designs)
    for search in (first, second):
        search.prune(1)
        search.extend(2)  # Budget 5 is absent from the input file.
        assert set(search.best) == {4, 6}
        assert all(len(tickets) == budget for budget, tickets in search.best.items())
    assert first.best == second.best and first.best_metrics == second.best_metrics
    assert first.origins == second.origins


def test_refiner_recertifies_before_writing_and_emits_portable_lf(refiner, tmp_path):
    baseline = _complete_eight(refiner)
    search = refiner.Search(8, {7: baseline})
    search.best[7] = _clustered_tickets()
    search.origins[7] = "corrupted after search"
    with pytest.raises(AssertionError):
        search.write(tmp_path)
    target = tmp_path / "H_8_6_4.txt"
    assert not target.exists()
    search.best[7] = [t[:] for t in baseline]
    assert search.write(tmp_path) == []
    raw = target.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    assert refiner._read_budget(target, 8) == {7: baseline}


def test_refiner_refuses_partial_or_duplicate_complete_seeds(refiner, tmp_path):
    target = tmp_path / "C_8_6_4.txt"
    target.write_text("1 2 3 4 5 6\n", encoding="ascii")
    with pytest.raises(ValueError, match="Invalid complete cover"):
        refiner._read_complete(target, 8)
    baseline = _complete_eight(refiner)
    rows = baseline + [baseline[0]]
    target.write_text("\n".join(" ".join(map(str, t)) for t in rows), encoding="ascii")
    with pytest.raises(ValueError, match="Invalid complete cover"):
        refiner._read_complete(target, 8)


def test_refiner_rejects_whole_file_if_a_budget_section_is_malformed(refiner, tmp_path):
    target = tmp_path / "H_8_6_4.txt"
    baseline = _complete_eight(refiner)
    text = "B 7\n" + "\n".join(" ".join(map(str, t)) for t in baseline)
    target.write_text(text + "\nB 2\n1 2 3 4 5 6\n", encoding="ascii")
    with pytest.raises(ValueError, match="Missing, duplicate, or malformed"):
        refiner._read_budget(target, 8)
