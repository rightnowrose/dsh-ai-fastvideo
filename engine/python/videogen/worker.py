"""worker：从 SQLite 队列里领任务、出片、写回状态。

两种用法：
  * 独立进程：`python -m videogen.cli worker`（常驻，单并发）
  * 插件内：AstrBot 的 video_gen 插件用 asyncio 把 run_job 丢进线程池，
    共用同一个数据库，所以即使插件重启，未完成的任务也不会丢。
"""

from __future__ import annotations

import time
import traceback
from pathlib import Path

from . import jobs, pipeline
from .spec import Spec


def run_job(job_id: int, on_progress=None) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise KeyError(f"任务不存在：{job_id}")

    spec = Spec.from_dict(jobs.spec_of(job))
    started = time.time()
    jobs.update(job_id, status="running", stage="准备", progress=0.0)

    def progress(stage: str, pct: float) -> None:
        jobs.update(job_id, stage=stage, progress=round(pct, 3))
        if on_progress:
            try:
                on_progress(stage, pct)
            except Exception:  # noqa: BLE001
                pass

    try:
        result = pipeline.build(spec, progress=progress)
    except Exception as exc:  # noqa: BLE001
        detail = (str(exc) or exc.__class__.__name__)[:800]
        jobs.update(
            job_id,
            status="failed",
            stage="失败",
            err=detail + "\n" + traceback.format_exc(limit=3)[-500:],
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        raise

    jobs.update(
        job_id,
        status="done",
        stage="完成",
        progress=1.0,
        out_path=str(result["out"]),
        duration=float(result["duration"]),
        size=int(result["size"]),
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    result["job_id"] = job_id
    result["elapsed"] = round(time.time() - started, 2)
    return result


def loop(poll: float = 1.5, once: bool = False) -> None:
    jobs.init()
    requeued = jobs.reset_stale()
    if requeued:
        print(f"[worker] 回收 {requeued} 个卡住的任务")
    while True:
        job = jobs.claim_next()
        if not job:
            if once:
                print("[worker] 队列空，退出")
                return
            time.sleep(poll)
            continue
        jid = int(job["id"])
        print(f"[worker] 开始 #{jid} {job.get('style')} {job.get('user_id')}")
        try:
            res = run_job(jid)
            print(f"[worker] #{jid} 完成 {Path(res['out']).name} {res['duration']}s {res['size'] / 1024:.0f}KB")
        except Exception as exc:  # noqa: BLE001
            print(f"[worker] #{jid} 失败：{exc}")
        if once:
            return
