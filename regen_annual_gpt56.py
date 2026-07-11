#!/usr/bin/env python3
"""Regenerate an existing annual selection with GPT-5.6-Sol in staging."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path


REPO = Path("/root/paper-radar")
PYTHON = REPO / ".venv/bin/python"
ID_RE = re.compile(r"\d{4}\.\d{4,5}")
PRINT_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()


def valid_markdown(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 1000


def run(command: list[str], timeout: int, env: dict[str, str] | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            command,
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip()
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, f"timeout after {timeout}s\n{output.strip()}"


def tail(text: str, count: int = 3) -> str:
    return " | ".join(text.splitlines()[-count:])


def atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--annual",
        default="/root/code/paper_reading_walkstream/digests/annual-2026-05-07",
    )
    parser.add_argument("--stage", default="")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    annual = Path(args.annual)
    source = annual / "deepreads"
    stage = Path(args.stage) if args.stage else annual / "deepreads_gpt56_20260711"
    if source.resolve() == stage.resolve():
        raise SystemExit("refusing to use the live deepreads directory as staging")
    stage.mkdir(parents=True, exist_ok=True)
    ids = sorted(path.stem for path in source.glob("*.md") if ID_RE.fullmatch(path.stem))
    if not ids:
        raise SystemExit(f"no annual arXiv deep-reads found under {source}")

    manifest_path = stage / "manifest.json"
    manifest = {
        "model": "gpt-5.6-sol",
        "annual": str(annual),
        "source_count": len(ids),
        "started_at": time.time(),
        "results": {},
    }
    if manifest_path.exists() and not args.force:
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["started_at"] = old.get("started_at", manifest["started_at"])
            manifest["results"] = old.get("results", {})
        except Exception:
            pass

    review_env = os.environ.copy()
    review_env["REVIEW_MODEL"] = "gpt-5.6-sol"

    def record(aid: str, payload: dict) -> None:
        with STATE_LOCK:
            manifest["results"][aid] = payload
            manifest["updated_at"] = time.time()
            atomic_json(manifest_path, manifest)

    def process(aid: str) -> tuple[str, bool]:
        started = time.time()
        out = stage / f"{aid}.md"
        review = stage / f"{aid}.review.json"
        details: list[str] = []
        try:
            if args.force or not valid_markdown(out):
                tmp = stage / f".{aid}.md.tmp"
                code, output = run(
                    [str(PYTHON), "deepread_gpt56.py", aid, str(tmp)],
                    timeout=1400,
                )
                details.append(f"generate[{code}] {tail(output)}")
                if code != 0 or not valid_markdown(tmp):
                    record(aid, {"ok": False, "step": "generate", "detail": details, "seconds": round(time.time() - started, 1)})
                    return aid, False
                os.replace(tmp, out)
            else:
                details.append("generate[resume]")

            code, output = run([str(PYTHON), "add_figure.py", aid, str(out)], timeout=180)
            details.append(f"figure[{code}] {tail(output, 1)}")

            if args.force or not review.is_file():
                code, output = run(
                    [str(PYTHON), "review_gpt.py", aid, str(out), str(review)],
                    timeout=700,
                    env=review_env,
                )
                details.append(f"review[{code}] {tail(output)}")
                if code != 0 or not review.is_file():
                    record(aid, {"ok": False, "step": "review", "detail": details, "seconds": round(time.time() - started, 1)})
                    return aid, False

            audit = json.loads(review.read_text(encoding="utf-8"))
            revised = False
            if float(audit.get("score", 10)) < 7.5 and audit.get("issues"):
                code, output = run(
                    [str(PYTHON), "revise_gpt56.py", aid, str(out), str(review)],
                    timeout=1400,
                )
                details.append(f"revise[{code}] {tail(output)}")
                if code == 0:
                    revised = True
                    code, output = run(
                        [str(PYTHON), "review_gpt.py", aid, str(out), str(review)],
                        timeout=700,
                        env=review_env,
                    )
                    details.append(f"rereview[{code}] {tail(output)}")
                    if code != 0:
                        record(aid, {"ok": False, "step": "rereview", "detail": details, "seconds": round(time.time() - started, 1)})
                        return aid, False
                    audit = json.loads(review.read_text(encoding="utf-8"))

            payload = {
                "ok": True,
                "bytes": out.stat().st_size,
                "review_score": audit.get("score"),
                "revised": revised,
                "seconds": round(time.time() - started, 1),
                "detail": details,
            }
            record(aid, payload)
            return aid, True
        except Exception as exc:  # noqa: BLE001
            details.append(f"exception: {type(exc).__name__}: {exc}")
            record(aid, {"ok": False, "step": "exception", "detail": details, "seconds": round(time.time() - started, 1)})
            return aid, False

    print(f"annual GPT-5.6 regeneration: {len(ids)} papers, workers={args.workers}, stage={stage}", flush=True)
    completed = 0
    failed: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(process, aid): aid for aid in ids}
        for future in concurrent.futures.as_completed(futures):
            aid, ok = future.result()
            completed += 1
            if not ok:
                failed.append(aid)
            with PRINT_LOCK:
                status = "ok" if ok else "FAIL"
                print(f"[{completed}/{len(ids)}] {status:4s} {aid}", flush=True)

    manifest["finished_at"] = time.time()
    manifest["failed"] = failed
    manifest["ok_count"] = len(ids) - len(failed)
    atomic_json(manifest_path, manifest)
    print(f"finished: {len(ids) - len(failed)}/{len(ids)} ok; failed={failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
