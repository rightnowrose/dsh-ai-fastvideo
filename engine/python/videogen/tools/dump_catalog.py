"""给 DSH 面板用：把风格提示词目录 dump 成一行 JSON（stdout）。

    python tools/dump_catalog.py [--force]

输出形如 {"count":341,"categories":[...],"styles":[{key,code,name,category,look,fit,keywords,thumb,prompt}...]}
面板的 Host 侧拿这份数据渲染风格网格，不会每次点开都重扫目录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
APPS = HERE.parents[2]  # engine/python，videogen 包的父目录
if str(APPS) not in sys.path:
    sys.path.insert(0, str(APPS))

from videogen import prompts  # noqa: E402


def main() -> int:
    # Windows 控制台/管道默认 GBK，中文提示词直接写会 UnicodeEncodeError；
    # Host 侧虽然注入了 PYTHONIOENCODING，脚本自己也兜一层。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    data = prompts.catalog(force="--force" in sys.argv)
    # 提示词正文很长（341 条 × 几千字），面板不需要全文，只留前 400 字做预览
    slim = []
    for s in data["styles"]:
        item = dict(s)
        item.pop("md", None)
        # 缩略图的绝对路径不出本机：面板用 key 走 /videogen/thumb，
        # 这里只告诉它「有没有」。
        item["hasThumb"] = bool(item.pop("thumb", ""))
        item["prompt"] = (item.get("prompt") or "")[:400]
        slim.append(item)
    out = {
        "count": len(slim),
        "root": data.get("root", ""),
        "categories": data.get("categories", []),
        "styles": slim,
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
