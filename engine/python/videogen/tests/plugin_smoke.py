"""插件链路冒烟测试：不连 QQ，直接驱动 video_gen 的 LLM 工具 + 自带 worker。

跑法（用 AstrBot 的 venv）：
    F:\\Deepseek-Chat\\apps\\AstrBot\\.venv\\Scripts\\python.exe apps/videogen/tests/plugin_smoke.py

覆盖：
  1. 插件 import + `@filter.llm_tool` 注册不炸；
  2. initialize() 能把库里 pending 的任务捞起来跑（模拟重启恢复）；
  3. make_video 立刻返回、当场入队，worker 在线程里真的出片；
  4. 两条任务各出一次片（去重生效），各自包成 [Plain, Video] 发回原会话。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

APPS = Path(r"F:\Deepseek-Chat\apps")
PLUGIN = APPS / "AstrBot" / "data" / "plugins" / "video_gen" / "main.py"
sys.path.insert(0, str(APPS))

spec_loader = importlib.util.spec_from_file_location("video_gen_main", PLUGIN)
assert spec_loader and spec_loader.loader
mod = importlib.util.module_from_spec(spec_loader)
spec_loader.loader.exec_module(mod)

from videogen import jobs  # noqa: E402
from videogen.spec import Spec, split_words  # noqa: E402
from videogen.styles import resolve  # noqa: E402


class FakeContext:
    def __init__(self):
        self.sent: list[tuple[str, object]] = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))
        return True


USER_ID = str(900000 + int(time.time()) % 90000)

class FakeEvent:
    unified_msg_origin = "default:FriendMessage:10001"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return ""

    def get_sender_id(self):
        return USER_ID

    def get_message_str(self):
        return "视频 你|懂什么|听话"

    def plain_result(self, text):
        return text


def seed_pending() -> int:
    """塞一条 pending 任务进库，模拟「上次崩了还没跑」。"""
    spec = Spec.from_dict(
        {
            "style": "a3",
            "lines": [{"text": w} for w in split_words("别|熬夜|早点睡")],
            "voice": {"enabled": True},
            "bgm": {"enabled": False},
        }
    )
    resolve(spec)
    spec.validate()
    return jobs.enqueue(
        spec.to_dict(),
        platform="aiocqhttp",
        chat_id="",
        user_id=USER_ID,
        umo="default:FriendMessage:10001",
    )


async def wait_job(job_id: int, timeout: float = 300.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = jobs.get(job_id) or {}
        if str(job.get("status")) in ("done", "failed"):
            return job
        await asyncio.sleep(1)
    return {"status": "timeout"}


async def main() -> int:
    ctx = FakeContext()
    plugin = mod.Main(ctx, {"max_per_hour": 99})

    # 先把「旧任务」塞进库，initialize 应该把它捞起来
    recovered = seed_pending()
    print(f"[0] 预置 pending 任务 #{recovered}")

    await plugin.initialize()
    event = FakeEvent()
    reply = await plugin.make_video(event, "你|懂什么|听话", "a3", "")
    print(f"[1] 工具立即返回：{reply}")
    assert "开工" in reply, reply

    rows = jobs.recent(limit=1)
    fresh = int(rows[0]["id"])
    print(f"[2] 当场入队 #{fresh}")
    assert fresh != recovered

    t0 = time.time()
    old_job = await wait_job(recovered)
    new_job = await wait_job(fresh)
    for tag, job in (("恢复", old_job), ("新单", new_job)):
        print(
            f"[3] {tag} #{job.get('id')} -> {job.get('status')}  "
            f"{job.get('duration')}s  {int(job.get('size') or 0) / 1024:.0f}KB  "
            f"用时 {time.time() - t0:.1f}s"
        )
        if job.get("status") == "failed":
            print("    err:", (job.get("err") or "")[:400])
    assert old_job.get("status") == "done", old_job.get("status")
    assert new_job.get("status") == "done", new_job.get("status")
    assert old_job.get("out_path") != new_job.get("out_path"), "两条任务出了同一个文件，去重有问题"

    for _ in range(30):
        if len(ctx.sent) >= 2:
            break
        await asyncio.sleep(0.5)
    print(f"[4] 回发消息 {len(ctx.sent)} 条")
    for umo, chain in ctx.sent:
        print(f"    {umo} -> {[type(c).__name__ for c in chain.chain]}")
    assert len(ctx.sent) == 2, f"期望 2 条（一条一个任务），实际 {len(ctx.sent)}"
    assert all(
        any(type(c).__name__ == "Video" for c in chain.chain) for _, chain in ctx.sent
    ), "回发消息里没有视频段"

    await plugin.terminate()
    print("\n插件链路 OK（初始化恢复 + 工具入队 + 去重 + 回发视频）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
