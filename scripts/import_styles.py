"""把一个风格库目录（一堆 `代号_名称.md` + 同名 jpg）批量导进本包的 assets/styles。

用法：
    python scripts/import_styles.py <源目录> [--into assets/styles] [--dry-run] [--overwrite]
    python scripts/import_styles.py ../代码动画/风格提示词 --dry-run

源目录可以带分类子目录（`A-G_代码样片/A3_大字冲击.md`）；默认把「分类目录名_文件名」
扁平化，因为面板按 `分类/代号` 识别风格。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = PKG_DIR / "assets" / "styles"


def collect(source: Path) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    if not source.is_dir():
        return found
    for md in sorted(source.rglob("*.md")):
        if md.name.lower() in {"readme.md", "license.md", "说明.md", "许可与来源.md"}:
            continue
        parent = md.parent.name
        flat = f"{parent}__{md.name}" if parent and parent != source.name else md.name
        found.append((md, flat))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导入风格提示词")
    ap.add_argument("source", help="风格库目录")
    ap.add_argument("--into", default=str(DEFAULT_TARGET), help="目标目录（默认 assets/styles）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不复制")
    ap.add_argument("--overwrite", action="store_true", help="覆盖同名文件")
    args = ap.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    target = Path(args.into).expanduser().resolve()
    items = collect(source)
    if not items:
        print(f"没在 {source} 里找到 *.md")
        return 1

    print(f"源：{source}")
    print(f"目标：{target}")
    print(f"发现 {len(items)} 条风格")

    copied = skipped = 0
    for md, flat in items:
        dest = target / flat
        jpg = md.with_suffix(".jpg")
        if dest.exists() and not args.overwrite:
            skipped += 1
            print(f"  skip  {flat}")
            continue
        print(f"  add   {flat}{'  (+jpg)' if jpg.exists() else ''}")
        if args.dry_run:
            copied += 1
            continue
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(md, dest)
        if jpg.exists():
            shutil.copy2(jpg, target / f"{Path(flat).stem}.jpg")
        copied += 1

    print(f"\n{'将复制' if args.dry_run else '已复制'} {copied} 条，跳过 {skipped} 条")
    if not args.dry_run:
        print("提示：面板 60 秒后会重新扫目录，或点面板里的「刷新」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
