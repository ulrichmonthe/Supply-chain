"""Capture every API response the frontend needs, as static JSON.

Driven against the real backend so the demo answers exactly what the live tool
answers. Nothing here is hand-written; re-run it if the model changes.

Two passes matter:
  1. run every scenario once at its own settings, so every later snapshot shows a
     fully populated scorecard rather than half the rows saying "not run yet";
  2. for each scenario and month, pin the month, re-solve, and record the state
     the interface would be in.

The opening state is recorded between the two, not after them. Capturing it at the
end meant reconstructing it by putting the levers back, which is only correct if the
database started clean — and this script is what makes it dirty. A capture run that
died half way through the month sweep left the next run treating February as the
baseline's own setting, and the published demo then opened on a February the landing
page described as an ordinary year.
"""
import json, os, pathlib, sys, urllib.request, urllib.error

BASE = os.environ.get("API", "http://127.0.0.1:8000/api")
OUT = pathlib.Path(sys.argv[1])
MONTHS = [None] + list(range(1, 13))
results_seen = set()


def call(path, method="GET", body=None):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=180) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def save(rel, payload):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, separators=(",", ":")))


def snapshot(key):
    """Record the scenario list, the scorecard, and every result they point at."""
    scenarios = call("/countries/1/scenarios")
    save(f"snap/{key}.json", {"scenarios": scenarios, "scorecard": call("/countries/1/scorecard")})
    for s in scenarios:
        rid = s.get("latest_result_id")
        if rid and rid not in results_seen:
            results_seen.add(rid)
            save(f"results/{rid}.json", call(f"/results/{rid}"))


for rel, path in [
    ("countries.json", "/countries"),
    ("overview.json", "/countries/1/overview"),
    ("nodes.json", "/countries/1/nodes"),
    ("edges.json", "/countries/1/edges"),
    ("basemap.json", "/countries/1/basemap.geojson"),
    ("audit.json", "/countries/1/audit"),
    ("connectors.json", "/connectors"),
    ("connections.json", "/countries/1/connections"),
]:
    save(rel, call(path))
for m in range(1, 13):
    save(f"season/{m}.json", call(f"/countries/1/season/{m}"))
print("static data captured")

scenarios_now = call("/countries/1/scenarios")
original = {s["id"]: (s.get("levers") or {}) for s in scenarios_now}

# The opening state is read from the live database, so anything that has touched the
# app first is baked into what visitors see. The accessibility check presses a month
# cell; a half-finished capture leaves its own pin behind. Either way the demo then
# greets everyone with February under the heading "as it operates today". Refuse.
for s in scenarios_now:
    if s.get("is_baseline") and (s.get("levers") or {}).get("month"):
        sys.exit(
            f"The baseline is pinned to month {s['levers']['month']}. This database has been "
            "used since it was seeded, and capturing now would publish that month as the "
            "baseline.\nStart again from a fresh one:\n"
            "  rm -f backend/var/hscn.db backend/var/hscn.db-wal backend/var/hscn.db-shm\n"
            "  make run   # in another terminal, then re-run this"
        )

# Pass 1 — everything has a result before any snapshot is taken.
for sid in sorted(original):
    call(f"/scenarios/{sid}/run", "POST")
print("all scenarios solved once")
for sid in sorted(original):
    pinned = original[sid].get("month")
    if pinned:
        print(f"  note: scenario {sid} is pinned to month {pinned}")

# The state on first load, taken while the levers are still the seeded ones.
snapshot("initial")

# Pass 2 — the state behind every scenario/month the interface can reach.
for sid in sorted(original):
    for month in MONTHS:
        call(f"/scenarios/{sid}", "PATCH", {"levers": {**original[sid], "month": month}})
        call(f"/scenarios/{sid}/run", "POST")
        snapshot(f"{sid}-{'annual' if month is None else month}")
    print(f"  scenario {sid}: 13 months")

# Roadmaps, including the baseline's deliberate refusal.
for sid in sorted(original):
    try:
        save(f"roadmap/{sid}.json", call(f"/scenarios/{sid}/roadmap"))
    except urllib.error.HTTPError as e:
        save(f"roadmap/{sid}.json", {"__status": e.code, "detail": json.loads(e.read())["detail"]})

# Leave the database as it was found, so the next capture starts from the seeded
# settings rather than from wherever the month sweep stopped.
for sid, levers in original.items():
    call(f"/scenarios/{sid}", "PATCH", {"levers": levers})
    call(f"/scenarios/{sid}/run", "POST")

# Result ids are database rows, so they change every capture. Files from an earlier
# run are unreachable but still shipped, and the demo doubled in size before anyone
# noticed. Anything no snapshot points at goes.
live = {f"{rid}.json" for rid in results_seen}
for stale in (OUT / "results").glob("*.json"):
    if stale.name not in live:
        stale.unlink()
        print(f"  pruned orphaned {stale.name}")

files = list(OUT.rglob("*.json"))
print(f"\n{len(files)} files, {sum(f.stat().st_size for f in files)/1024/1024:.1f} MB")
print(f"results captured: {len(results_seen)}")
