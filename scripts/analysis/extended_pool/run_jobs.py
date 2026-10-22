"""Run pipeline jobs with a small process pool. Usage: run_jobs.py JOBFILE WORKERS"""
import json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
HERE = Path(__file__).resolve().parent
PY = sys.executable  # interpretorul care rulează scriptul (venv-ul proiectului)
jobs = json.loads(Path(sys.argv[1]).read_text())
workers = int(sys.argv[2])
(HERE / "runs").mkdir(exist_ok=True)

def run(j):
    name = j["name"]
    out = HERE / "runs" / f"{name}.json"
    if out.exists():
        return name, "skip"
    log = HERE / "runs" / f"{name}.log"
    args = [PY, str(HERE / "pipeline.py"), j["game"], str(j["budget"]), str(j["v"]), str(j["seconds"]),
            str(j.get("seed", 1)), str(out), j.get("seed_kind", "greedy"),
            str(j.get("lam3", 0.01)), str(j.get("lam5", 0.01)), str(j.get("w4mult", 1.0)), str(j.get("hard_pack", 0))]
    t = time.time()
    with open(log, "w") as f:
        rc = subprocess.call(args, stdout=f, stderr=subprocess.STDOUT, cwd=str(HERE))
    return name, f"rc={rc} {time.time()-t:.0f}s"

with ThreadPoolExecutor(workers) as ex:
    for name, st in ex.map(run, jobs):
        print(name, st, flush=True)
