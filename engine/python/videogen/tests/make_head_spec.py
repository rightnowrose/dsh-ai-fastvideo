import json
from pathlib import Path

src = Path(r"F:\Deepseek-Chat\data\video_out\_p\explain.json")
d = json.loads(src.read_text(encoding="utf-8"))
d["lines"] = d["lines"][:2]
d["voice"] = {"enabled": False}
d["bgm"] = {"enabled": False}
dst = Path(r"F:\Deepseek-Chat\data\video_out\_p\explain_head.json")
dst.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", dst)
