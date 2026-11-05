"""Read-only verification of completed, disjoint pytest JUnit batches.

Does not run or collect tests. Requires the runner's completed summary.json
and every batchN.xml. Checks every root test module is assigned exactly once,
every collecting module appears in its JUnit file, identities never overlap,
and JUnit counters agree with the actual testcase elements. Two existing
utility files without pytest test functions are explicitly reported separately.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

NON_COLLECTING = {
    'test_integration.py': 'Assertions execute during collection/import; no pytest testcase functions.',
    'test_tfm_hits.py': 'Manual main entry point; pytest imports this file but does not run main.',
}


def verify(directory, source):
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    expected = set(summary["files"])
    current = {p.name for p in source.glob("test_*.py")}
    assert len(summary["files"]) == len(expected), "Duplicate source module names"
    assert expected == current, {
        "missing_from_run": sorted(current - expected),
        "missing_from_source": sorted(expected - current),
    }
    assignments = Counter(name for batch in summary["batches"] for name in batch)
    assert set(assignments) == expected and all(n == 1 for n in assignments.values())
    assert len(summary["exit_codes"]) == len(summary["batches"])
    assert all(code == 0 for code in summary["exit_codes"]), summary["exit_codes"]
    all_identities = set()
    seen_modules = set()
    seen_non_collecting = set()
    totals = Counter()
    reports = []
    for batch_number, names in enumerate(summary["batches"]):
        root = ET.parse(directory / f"batch{batch_number}.xml").getroot()
        cases = list(root.iter("testcase"))
        counts = Counter(tests=len(cases), passed=0, failed=0, errors=0, skipped=0)
        identities = set()
        modules = set()
        for case in cases:
            classname = case.get("classname", "")
            name = case.get("name", "")
            identity = (classname, name)
            assert identity not in identities, (batch_number, "duplicate testcase", identity)
            identities.add(identity)
            module = classname.split(".", 1)[0] + ".py"
            assert module in names, (batch_number, "unassigned testcase", identity)
            modules.add(module)
            failed = case.find("failure") is not None
            error = case.find("error") is not None
            skipped = case.find("skipped") is not None
            assert int(failed) + int(error) + int(skipped) <= 1, identity
            key = "failed" if failed else "errors" if error else "skipped" if skipped else "passed"
            counts[key] += 1
        unobserved = set(names) - modules
        assert unobserved <= NON_COLLECTING.keys(), (batch_number, "unobserved modules", sorted(unobserved))
        for filename in unobserved:
            tree = ast.parse((source / filename).read_text(encoding='utf-8'))
            assert not any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_')
                or isinstance(node, ast.ClassDef) and node.name.startswith('Test')
                for node in ast.walk(tree)
            ), (filename, 'Unexpected test definition in non-collecting module')
        seen_non_collecting.update(unobserved)
        assert not (all_identities & identities), (batch_number, "test identities overlap")
        assert not (seen_modules & modules), (batch_number, "test modules overlap")
        xml_counts = Counter()
        for suite in root.iter("testsuite"):
            for key in ("tests", "failures", "errors", "skipped"):
                xml_counts[key] += int(suite.get(key, "0"))
        assert xml_counts == Counter(
            tests=counts["tests"], failures=counts["failed"],
            errors=counts["errors"], skipped=counts["skipped"],
        ), (batch_number, "JUnit counter mismatch", xml_counts, counts)
        assert not counts["failed"] and not counts["errors"], (batch_number, counts)
        all_identities.update(identities)
        seen_modules.update(modules)
        totals.update(counts)
        reports.append({
            "batch": batch_number, "modules": len(modules), **dict(counts),
            "exit_code": summary["exit_codes"][batch_number],
            "module_names": sorted(modules),
            "non_collecting_module_names": sorted(unobserved),
        })
    assert seen_modules | seen_non_collecting == expected
    assert totals["tests"] == len(all_identities)
    return {
        "source_test_modules": len(expected),
        "observed_test_modules": len(seen_modules),
        "non_collecting_modules": {name: NON_COLLECTING[name] for name in sorted(seen_non_collecting)},
        "unique_test_identities": len(all_identities),
        "all_module_assignments_disjoint": True,
        "all_test_identities_disjoint": True,
        "all_runner_exit_codes_zero": True,
        "all_junit_counters_match": True,
        "totals": dict(totals),
        "runner_seconds": summary["seconds"],
        "batches": reports,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.directory, args.source)
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
