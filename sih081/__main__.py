"""Command line and read-only local dashboard."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .data import PLACES, demo_cases, fetch_cases, merge_observations, write_cases
from .forecast import evaluate
from .issue import issue

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="SIH26081 hybrid forecast blending pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Generate clearly labeled synthetic sample and evaluation")
    demo.add_argument("--days", type=int, default=240)
    fetch = sub.add_parser("fetch", help="Fetch run-specific archived forecasts and ERA5 comparison")
    fetch.add_argument("--start", required=True, help="UTC date YYYY-MM-DD, from 2026-04-02")
    fetch.add_argument("--days", type=int, default=100)
    fetch.add_argument("--regions", nargs="+", default=list(PLACES))
    fetch.add_argument("--rain-window", choices=("utc", "imd"), default="imd", help="24 h rain ending 03 UTC (08:30 IST) or 00 UTC")
    observations = sub.add_parser("merge-observations", help="Match quality-checked observations to archived forecast cases")
    observations.add_argument("observations", type=Path)
    observations.add_argument("--cases", type=Path, default=ROOT / "data" / "archive" / "cases.csv")
    observations.add_argument("--output", type=Path, default=ROOT / "data" / "observed")
    observations.add_argument("--strict", action="store_true", help="Require explicit units; validate quality, release timing and supplied support metadata")
    run = sub.add_parser("evaluate", help="Evaluate a case table with chronological splits")
    run.add_argument("cases", type=Path)
    run.add_argument("--manifest", type=Path)
    run.add_argument("--output", type=Path, default=ROOT / "output")
    issuance = sub.add_parser("issue", help="Issue one immutable forecast from an archived or current 00 UTC run")
    issuance.add_argument("--run", required=True, help="YYYY-MM-DDT00:00 UTC")
    issuance.add_argument("--regions", nargs="+", default=list(PLACES))
    issuance.add_argument("--replay", action="store_true", help="Label historical run replay explicitly; age assessed at run + 6 hours")
    issuance.add_argument("--cases", type=Path, default=ROOT / "data" / "archive" / "cases.csv")
    issuance.add_argument("--state", type=Path, default=ROOT / "output" / "state.json")
    issuance.add_argument("--output", type=Path, default=ROOT / "output" / "issued")
    refresh = sub.add_parser("refresh", help="Fetch a rolling archive, evaluate it, and issue today's 00 UTC run")
    refresh.add_argument("--regions", nargs="+", default=list(PLACES))
    refresh.add_argument("--run", help="Optional 00 UTC run date YYYY-MM-DDT00:00")
    serve = sub.add_parser("serve", help="Serve the local dashboard")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.command == "demo":
        cases = ROOT / "data" / "demo" / "cases.csv"
        write_cases(cases, demo_cases(args.days))
        manifest = {"kind": "synthetic_demo", "truth": "Synthetic observations; no real-world skill claim",
                    "availability": "Synthetic issue and observation release times", "cases": args.days * len(PLACES) * 2 * 3}
        (cases.parent / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        report = evaluate(cases, ROOT / "output" / "demo", manifest)
        print(f"Synthetic evaluation ready: {len(report['results'])} products, {len(report['cases'])} test cases")
    elif args.command == "fetch":
        manifest = fetch_cases(args.start, args.days, args.regions, ROOT / "data" / "archive", args.rain_window)
        print(json.dumps({k: v for k, v in manifest.items() if k != "failed_requests"}, indent=2))
        print(f"Failed requests: {len(manifest['failed_requests'])}")
    elif args.command == "merge-observations":
        manifest = merge_observations(args.cases, args.observations, args.output, strict=args.strict)
        print(f"Matched {manifest['cases']} forecast cases to quality-checked observations")
    elif args.command == "evaluate":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
        report = evaluate(args.cases, args.output, manifest)
        print(f"Evaluation ready: {len(report['results'])} products, {len(report['cases'])} test cases")
    elif args.command == "issue":
        result = issue(args.run, args.regions, args.cases, args.state, args.output, replay=args.replay)
        print(f"Issued {len(result['records'])} records; source failures: {len(result['source_errors'])}")
    elif args.command == "refresh":
        cycle = (datetime.fromisoformat(args.run).replace(tzinfo=timezone.utc) if args.run
                 else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
        if cycle.hour != 0 or cycle.minute != 0:
            raise ValueError("Refresh requires a 00 UTC run")
        latest = ROOT / "output" / "issued" / "latest.json"
        saved = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else {}
        active = ROOT / "output" / "state.json"
        if (saved.get("kind") == "research_guidance" and saved.get("run_initialized_at") == cycle.isoformat() and active.exists()
                and saved.get("validation_state_sha256") == hashlib.sha256(active.read_bytes()).hexdigest()
                and {r["region"] for r in saved.get("records", [])} == set(args.regions)):
            print(f"Already issued the {cycle.isoformat()} run")
            return
        if not args.run and datetime.now(timezone.utc) < cycle + timedelta(hours=6):
            raise ValueError("Run after 06:00 UTC so source forecasts have time to arrive")
        start = (cycle - timedelta(days=174)).date().isoformat()
        prior = json.loads(active.read_text(encoding="utf-8")) if active.exists() else {}
        archive = ROOT / "data" / "archive"
        with tempfile.TemporaryDirectory(prefix="refresh-", dir=ROOT / "data") as temporary:
            staging = Path(temporary)
            if (archive / "raw").exists():
                shutil.copytree(archive / "raw", staging / "raw")
            manifest = fetch_cases(start, 165, args.regions, staging, "imd")
            manifest["previously_reviewed_test_through"] = prior.get("coverage", {}).get("end", cycle.date().isoformat())
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            evaluate(staging / "cases.csv", staging / "evaluation", manifest)
            result = issue(cycle.isoformat(), args.regions, staging / "cases.csv",
                           staging / "evaluation" / "state.json", staging / "issued")
            for source in (staging / "issued").glob("forecast-*.*"):
                existing = ROOT / "output" / "issued" / source.name
                if existing.exists() and existing.read_bytes() != source.read_bytes():
                    raise FileExistsError(f"Refusing to overwrite immutable forecast: {existing}")
            # Fetch or evaluation failures leave the current dashboard snapshots intact.
            shutil.copytree(staging / "raw", archive / "raw", dirs_exist_ok=True)
            issued_root = ROOT / "output" / "issued"
            if (staging / "issued" / "source_payloads").exists():
                shutil.copytree(staging / "issued" / "source_payloads", issued_root / "source_payloads", dirs_exist_ok=True)
            for source in (staging / "issued").glob("forecast-*.*"):
                if not (issued_root / source.name).exists():
                    issued_root.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, issued_root / source.name)
            destinations = [(staging / "cases.csv", archive / "cases.csv"),
                            (staging / "manifest.json", archive / "manifest.json"),
                            (staging / "evaluation" / "state.json", active),
                            (staging / "issued" / "latest.json", latest)]
            backups = {}
            for i, (source, destination) in enumerate(destinations):
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    backups[destination] = staging / f"previous-{i}"
                    shutil.copyfile(destination, backups[destination])
                replacement = destination.with_suffix(destination.suffix + ".tmp")
                shutil.copyfile(source, replacement)
            promoted = []
            try:
                for source, destination in destinations:
                    destination.with_suffix(destination.suffix + ".tmp").replace(destination)
                    promoted.append(destination)
            except OSError:
                for destination in reversed(promoted):
                    if destination in backups:
                        backups[destination].replace(destination)
                    else:
                        destination.unlink(missing_ok=True)
                raise
        print(f"Refreshed {manifest['cases']} matched cases and issued {len(result['records'])} forecasts")
    else:
        web = ROOT / "web"

        class Handler(SimpleHTTPRequestHandler):
            def end_headers(self):
                self.send_header("Cache-Control", "no-cache")
                super().end_headers()
            def __init__(self, *inner_args, **kwargs):
                super().__init__(*inner_args, directory=str(web), **kwargs)

            def do_GET(self):
                if self.path in ("/api/state", "/api/issued"):
                    state = ROOT / "output" / ("state.json" if self.path == "/api/state" else "issued/latest.json")
                    if not state.exists():
                        self.send_error(404, "Run demo or evaluate first")
                        return
                    content = state.read_bytes()
                    if self.path == "/api/issued":
                        active_state = ROOT / "output" / "state.json"
                        if not active_state.exists() or json.loads(content).get("validation_state_sha256") != hashlib.sha256(active_state.read_bytes()).hexdigest():
                            self.send_error(503, "Forecast refresh is in progress; retry shortly")
                            return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    super().do_GET()

        print(f"Dashboard: http://127.0.0.1:{args.port} (listening on 0.0.0.0)")
        ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
