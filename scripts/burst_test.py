"""Phase 7a — burst/backpressure harness for the WikiStream consumer.

Feeds synthetic Wikimedia-format events to the REAL consumer (spawned as a
subprocess against a local SSE origin) at multiples of the observed baseline
rate, then asserts zero drops + correct dead-letter routing per level.

Stdlib-only (asyncio + urllib for the ClickHouse HTTP API). Deliberately not
k6 (vision doc §6). Usage:

    CH_PASSWORD=wikistream_dev_password \
    uv run --project consumer python scripts/burst_test.py

Environment: CH_HOST (localhost), CH_PORT (8123), CH_USER (wikistream),
CH_PASSWORD (required), CH_DB (default).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONSUMER_DIR = REPO_ROOT / "consumer"

CH_HOST = os.environ.get("CH_HOST", "localhost")
CH_PORT = os.environ.get("CH_PORT", "8123")
CH_USER = os.environ.get("CH_USER", "wikistream")
CH_PASSWORD = os.environ.get("CH_PASSWORD", "")
CH_DB = os.environ.get("CH_DB", "default")

# Malformed-injection ratios (must sum to <= 1.0).
INVALID_JSON_P = 0.004   # -> validation:invalid_json
BAD_TS_P = 0.003         # -> timestamp_unparseable
MISSING_FIELD_P = 0.003  # -> validation:missing

# Malformed variant name -> expected dead-letter reason.
VARIANT_REASON = {
    "invalid_json": "validation:invalid_json",
    "bad_ts": "timestamp_unparseable",
    "missing_field": "validation:missing",
}


def ch_query(sql: str, timeout: int = 60) -> list[list[str]]:
    """Run a SELECT against ClickHouse over the HTTP API; TSV -> rows.

    POST with the raw SQL as the body: this CH build does not parse
    form-encoded ``query=`` bodies (verified 2026-08-14).
    """
    auth = base64.b64encode(f"{CH_USER}:{CH_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        f"http://{CH_HOST}:{CH_PORT}/",
        data=sql.encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "text/plain",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return [line.split("\t") for line in body.splitlines() if line]


def ch_counts() -> dict[str, int]:
    """raw_events count + dead_letter count grouped by reason."""
    raw = int(ch_query(f"SELECT count() FROM {CH_DB}.raw_events")[0][0])
    dl: dict[str, int] = {}
    for row in ch_query(f"SELECT reason, count() FROM {CH_DB}.dead_letter GROUP BY reason"):
        dl[row[0]] = int(row[1])
    return {"raw": raw, **{f"dl:{k}": v for k, v in dl.items()}}


def make_event(n: int, malformed: str | None) -> str:
    """One recentchange-shaped JSON event (or a malformed variant)."""
    now_epoch = int(time.time())
    if malformed == "invalid_json":
        return f'{{"type": "edit", "wiki": "enwiki", "title": "Page {n}", "user": "U{n}", "bot": false, "length": {{"new": 100, "old": 90}}, "timestamp": {now_epoch}'
    if malformed == "bad_ts":
        return json.dumps({
            "type": "edit", "wiki": "enwiki", "title": f"Page {n}", "user": f"U{n}",
            "bot": False, "length": {"new": 100, "old": 90}, "timestamp": "not-a-date",
        })
    if malformed == "missing_field":
        return json.dumps({
            "type": "edit", "wiki": "enwiki", "user": f"U{n}",
            "bot": False, "length": {"new": 100, "old": 90}, "timestamp": now_epoch,
        })
    ev_type = "new" if n % 10 == 0 else "edit"
    return json.dumps({
        "type": ev_type, "wiki": "enwiki", "title": f"Page {n}", "user": f"U{n}",
        "bot": n % 7 == 0, "length": {"new": 100 + n % 500, "old": 90}, "timestamp": now_epoch,
    })


class BurstOrigin:
    """High-rate SSE origin: bulk-writes framed events to every connected reader."""

    def __init__(self, rate: float, duration_s: float, seed: int):
        self.rate = rate
        self.duration_s = duration_s
        self.rng = random.Random(seed)
        self.connected = asyncio.Event()
        self.ready = asyncio.Event()  # HTTP handshake done; SSE may start
        self._writers: set[asyncio.StreamWriter] = set()
        self.sent = 0
        self.valid_sent = 0
        self.injected: dict[str, int] = {}
        self.feed_start = 0.0
        self.feed_end = 0.0
        self.feed_done = asyncio.Event()
        self.close_conns = asyncio.Event()  # level over: drop the connections
        self._base_id = (seed + 1) * 10**9  # per-level unique id space

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # Minimal HTTP/1.1 SSE handshake — then feed() owns the write path.
        # Concurrent writes to one StreamWriter corrupt the SSE stream.
        try:
            await reader.readline()  # request line
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: keep-alive\r\n"
                b"Cache-Control: no-cache\r\n\r\n"
            )
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            writer.close()
            return
        self._writers.add(writer)
        self.connected.set()
        self.ready.set()
        try:
            # Keep the connection alive (pings) until the level is over, so the
            # consumer never sees a mid-drain stream end; it just gets a clean
            # EOF at terminate. Reconnect churn corrupted shutdown accounting.
            while not self.close_conns.is_set():
                if self.feed_done.is_set():
                    try:
                        writer.write(b": ping\r\n\r\n")
                        await writer.drain()
                    except (ConnectionResetError, BrokenPipeError):
                        break
                await asyncio.sleep(0.5)
        finally:
            self._writers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    async def feed(self):
        await self.ready.wait()
        self.feed_start = time.monotonic()
        deadline = self.feed_start + self.duration_s
        tick = 0.01
        # Wall-clock accumulator: int(rate*tick) truncation would cap e.g.
        # 565.5 -> 500/s. Accumulate against real elapsed time instead, so
        # sent tracks wall time and achieved_rate lands on target.
        acc = 0.0
        last = self.feed_start
        n = 0
        while True:
            now = time.monotonic()
            if now >= deadline:
                break
            acc += self.rate * (now - last)
            last = now
            per_tick = int(acc)
            acc -= per_tick
            if per_tick <= 0 or not self._writers:
                await asyncio.sleep(min(tick, max(0.0, deadline - now)))
                continue
            chunk = bytearray()
            for _ in range(per_tick):
                n += 1
                ev_id = self._base_id + n
                roll = self.rng.random()
                if roll < INVALID_JSON_P:
                    malformed = "invalid_json"
                elif roll < INVALID_JSON_P + BAD_TS_P:
                    malformed = "bad_ts"
                elif roll < INVALID_JSON_P + BAD_TS_P + MISSING_FIELD_P:
                    malformed = "missing_field"
                else:
                    malformed = None
                if malformed:
                    self.injected[malformed] = self.injected.get(malformed, 0) + 1
                else:
                    self.valid_sent += 1
                data = make_event(ev_id, malformed)
                chunk.extend(f"id: {ev_id}\r\ndata: {data}\r\n\r\n".encode())
            for w in list(self._writers):
                try:
                    w.write(chunk)
                    await w.drain()
                except (ConnectionResetError, BrokenPipeError):
                    # ponytail: a dead writer mid-burst is a real drop signal —
                    # the zero-drop assertion catches it; don't crash the level.
                    self._writers.discard(w)
            self.sent = n
            await asyncio.sleep(min(tick, max(0.0, deadline - now)))
        self.feed_end = time.monotonic()
        self.sent = n
        self.feed_done.set()


async def run_level(level: int, rate: float, duration_s: float, keep_state: bool) -> dict:
    seed = level
    origin = BurstOrigin(rate, duration_s, seed)
    server = await asyncio.start_server(origin.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    state_dir = tempfile.mkdtemp(prefix=f"burst-l{level}-")

    before = ch_counts()

    env = dict(os.environ)
    env.update({
        "STREAM_URL": f"http://127.0.0.1:{port}/",
        "CLICKHOUSE_HOST": CH_HOST,
        "CLICKHOUSE_PORT": CH_PORT,
        "CLICKHOUSE_USER": CH_USER,
        "CLICKHOUSE_PASSWORD": CH_PASSWORD,
        "STATE_DIR": state_dir,
    })
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "--project", str(CONSUMER_DIR), "python", "-m", "src.consumer",
        cwd=str(CONSUMER_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        await asyncio.wait_for(origin.connected.wait(), timeout=20)
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()
        server.close()
        raise RuntimeError(f"level {level}: consumer never connected")

    feed_task = asyncio.create_task(origin.feed())
    await feed_task

    # Give the consumer time to drain its socket buffer, then drop the
    # connections (clean FIN) and graceful-stop it.
    await asyncio.sleep(2.0)
    origin.close_conns.set()
    proc.terminate()  # SIGTERM -> final flush + save_state
    try:
        rc = await asyncio.wait_for(proc.wait(), timeout=20)
    except asyncio.TimeoutError:
        proc.kill()
        rc = -9
    server.close()
    consumer_log = (await proc.stdout.read()).decode(errors="replace")

    # Settle: async_insert buffers drain asynchronously; poll until stable.
    after: dict[str, int] | None = None
    stable = 0
    for _ in range(60):  # 0.5s apart, 30s cap
        cur = ch_counts()
        if cur == after:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        after = cur
        await asyncio.sleep(0.5)

    state_path = Path(state_dir) / "consumer_state.json"
    state_total = 0
    if state_path.exists():
        state_total = int(json.loads(state_path.read_text()).get("total", 0))

    elapsed = origin.feed_end - origin.feed_start
    achieved = origin.sent / elapsed if elapsed > 0 else 0.0
    sent, valid_sent, injected = origin.sent, origin.valid_sent, origin.injected

    raw_delta = (after or {}).get("raw", 0) - before["raw"]
    dl_delta = sum(
        (after or {}).get(k, 0) - before.get(k, 0)
        for k in (after or {})
        if k.startswith("dl:")
    )
    dl_by_reason = {
        k.removeprefix("dl:"): (after or {}).get(k, 0) - before.get(k, 0)
        for k in (after or {})
        if k.startswith("dl:")
    }
    for reason in ("validation:invalid_json", "timestamp_unparseable", "validation:missing"):
        dl_by_reason.setdefault(reason, 0)

    results = {
        "level": level, "target_rate": rate, "duration_s": duration_s,
        "sent": sent, "valid_sent": valid_sent, "injected": injected,
        "achieved_rate": round(achieved, 1), "rc": rc,
        "raw_delta": raw_delta, "dl_delta": dl_delta, "dl_by_reason": dl_by_reason,
        "state_total": state_total, "state_path": state_path if keep_state else "",
        "consumer_log": consumer_log,
        "drop_ok": raw_delta + dl_delta == sent,
        "dl_ok": all(
            dl_by_reason.get(VARIANT_REASON.get(r, r)) == c
            for r, c in injected.items()
        ),
        "state_ok": state_total == valid_sent,
        "rate_ok": achieved >= 0.95 * rate,
        "rc_ok": rc == 0,
    }
    results["pass"] = all(
        results[k] for k in ("drop_ok", "dl_ok", "state_ok", "rate_ok", "rc_ok")
    )
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=float, default=565.5, help="observed baseline ev/s")
    ap.add_argument("--multiples", default="2,5,10", help="burst multiples of baseline")
    ap.add_argument("--duration", type=float, default=60.0, help="seconds per level")
    ap.add_argument("--keep-state", action="store_true", help="keep per-level state dirs")
    args = ap.parse_args()

    if not CH_PASSWORD:
        print("CH_PASSWORD env var required", file=sys.stderr)
        return 2

    levels = [float(m) * args.baseline for m in args.multiples.split(",")]
    print(f"baseline {args.baseline:.1f} ev/s | levels: "
          + ", ".join(f"{m}x={r:.0f}/s" for m, r in zip(args.multiples.split(","), levels))
          + f" | {args.duration:.0f}s each | injection: "
          + f"invalid_json {INVALID_JSON_P:.1%} bad_ts {BAD_TS_P:.1%} missing_field {MISSING_FIELD_P:.1%}")

    all_results = []
    for mult, rate in zip(args.multiples.split(","), levels):
        try:
            res = asyncio.run(run_level(int(mult), rate, args.duration, args.keep_state))
        except Exception as e:  # noqa: BLE001 — harness must report and continue
            print(f"level {mult}x: ERROR {e}")
            all_results.append({"level": mult, "pass": False, "error": str(e)})
            continue
        all_results.append(res)
        verdict = "PASS" if res["pass"] else "FAIL"
        print(f"level {mult}x ({res['target_rate']:.0f} ev/s, {res['duration_s']:.0f}s): "
              f"{verdict} | sent {res['sent']} (valid {res['valid_sent']}) "
              f"| achieved {res['achieved_rate']:.0f} ev/s | rc {res['rc']} "
              f"| raw_delta {res['raw_delta']} dl_delta {res['dl_delta']} "
              f"| state {res['state_total']} | drops {(res['sent'] - res['raw_delta'] - res['dl_delta']) if res['drop_ok'] else 'MISMATCH'} "
              f"| dl {res['dl_by_reason']}")
        if not res["pass"] and res.get("consumer_log"):
            print("  consumer log tail:")
            for line in res["consumer_log"].strip().splitlines()[-12:]:
                print(f"    {line}")

    print("\nsummary:")
    print(f"{'level':>6} {'target/s':>9} {'achieved/s':>10} {'sent':>8} {'raw_delta':>9} {'dl_delta':>8} {'state':>7}  verdict")
    for r in all_results:
        if "error" in r:
            print(f"{r['level']:>6} {'-':>9} {'-':>10} {'-':>8} {'-':>9} {'-':>8} {'-':>7}  ERROR {r['error']}")
        else:
            print(f"{r['level']:>6} {r['target_rate']:>9.0f} {r['achieved_rate']:>10.0f} {r['sent']:>8} "
                  f"{r['raw_delta']:>9} {r['dl_delta']:>8} {r['state_total']:>7}  "
                  f"{'PASS' if r['pass'] else 'FAIL'}")

    return 0 if all(r.get("pass", False) for r in all_results) else 1


if __name__ == "__main__":
    sys.exit(main())
