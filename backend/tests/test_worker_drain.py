"""A deploy sends SIGTERM: the worker finishes the job in hand, takes no new one, exits 0."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = """
import sys, time
from backend import worker
calls = []
def run_one(owner, kinds=None):
    calls.append(1)
    worker._BUSY.set()
    print("ready", flush=True)  # parent sends SIGTERM only after this marker
    time.sleep(1.5)  # a job in progress when SIGTERM arrives
    print("finished", len(calls), flush=True)
    worker._BUSY.clear()
    return "job"
worker.run_one = run_one
sys.exit(worker.main([]))
"""


def test_sigterm_lets_the_running_job_finish_and_takes_no_new_one():
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            str(Path(__file__).resolve().parents[2] / d)
            for d in ("backend", "shared", "monitoring", "standards")
        ),
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", SCRIPT], stdout=subprocess.PIPE, text=True, env=env
    )
    assert proc.stdout.readline().strip() == "ready"  # wait for handler + job entry
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=20)
    assert proc.returncode == 0
    assert out.strip().splitlines() == ["finished 1"]


IDLE_HUNG = """
import sys, time
from backend import worker
def run_one(owner, kinds=None):
    print("ready", flush=True)  # worker loop reached a claim before SIGTERM
    time.sleep(600)  # idle worker stuck in a claim/poll that never returns
worker.run_one = run_one
sys.exit(worker.main([]))
"""


def test_sigterm_stops_an_idle_worker_at_once_even_if_its_poll_hangs():
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            str(Path(__file__).resolve().parents[2] / d)
            for d in ("backend", "shared", "monitoring", "standards")
        ),
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", IDLE_HUNG], stdout=subprocess.PIPE, text=True, env=env
    )
    assert proc.stdout.readline().strip() == "ready"  # handler installed + poll entered
    started = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=10) == 0
    assert time.monotonic() - started < 5
