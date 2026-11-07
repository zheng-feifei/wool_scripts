#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Loon 去广告规则裁剪器（幂等，可重复运行，重复运行不会产生副作用）

用途
  1. 每次同步上游后重新应用自定义裁剪，避免改动被上游覆盖
  2. 生成 custom/loon/ 下的"稳定订阅版"文件。上游永远不会写这个目录，
     所以订阅 custom/ 路径时，GitHub 网页点 Sync fork 也不会改动你的插件。

同步上游由 GitHub Actions 自动完成（.github/workflows/sync-upstream.yml）：
  每天定时 / 手动触发 / 修改裁剪配置时触发
  合并上游 -> 备份并恢复 custom/ -> 本脚本重新生成 -> verify_custom.py 守卫校验
  -> 全部通过才 push。任何一步失败，远程 main 完全不变，订阅地址不会被覆盖。
本地应急备用：bash custom/sync-upstream.sh

用法（在仓库根目录执行）
  python custom/prune_ads.py                    # 从 Loon/ 读取 -> 生成到 custom/loon/（推荐）
  python custom/prune_ads.py --in-place        # 就地裁剪 Loon/ 下的源文件（会与上游产生合并冲突，不推荐）
  python custom/prune_ads.py --src DIR --dst DIR
  python custom/prune_ads.py --dry-run         # 只报告会删多少行，不写文件

要增减需要排除的 App，只改下面四个配置项：
  BLOCK_KEYWORDS / ARG_TAG_KEYWORDS / DROP_HOSTS / DROP_LINE_RE
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 配置区 ----
# 块头（"# > xxx"）包含以下任一关键词，整块删除
BLOCK_KEYWORDS = ("哔哩哔哩", "掌上英雄联盟", "12123")

# [Argument] 中 tag= 命中以下关键词的开关，整行删除
ARG_TAG_KEYWORDS = ("bilibili", "哔哩哔哩")

# [MITM] 中需要剔除的主机名
# 注意：us.l.qq.com 必须保留，QQ音乐等应用仍在用
DROP_HOSTS = {
    # 哔哩哔哩 / 哔哩哔哩漫画
    "grpc.biliapi.net",
    "app.bilibili.com",
    "api.bilibili.com",
    "api.live.bilibili.com",
    "line3-h5-mobile-api.biligame.com",
    "manga.bilibili.com",
    "i*.hdslb.com",
    # 掌上英雄联盟
    "mlol.qt.qq.com",
    "ossweb-img.qq.com",
    # 12123（交管12123）
    "gab.122.gov.cn",
}

# 兜底：即使上游改了块头名字，正文里出现这些特征的非注释行也会被删掉
DROP_LINE_RE = re.compile(
    r"bilibili|biliapi\.net|hdslb\.com|biligame\.com"
    r"|mlol\.qt\.qq\.com|ossweb-img\.qq\.com|us\.l\.qq\.com\\/exapp"
    r"|gab\.122\.gov\.cn|12123",
    re.IGNORECASE,
)

# 需要裁剪的文件：源文件 -> 稳定订阅版目标文件
FILE_MAP = {
    "Loon/plugin/blockAds.plugin": "custom/loon/blockAds.plugin",
    "Loon/plugin/blockAdsV2.plugin": "custom/loon/blockAdsV2.plugin",
    "Loon/rule/rejectAd.list": "custom/loon/rejectAd.list",
}

# 插件头部的 raw-url 改成自己的稳定地址，避免 Loon 内点"更新"时拉回上游原版
RAW_URL_BASE = (
    "https://raw.githubusercontent.com/zheng-feifei/wool_scripts/main/custom/loon/"
)

DESC_MARKER = "(已移除：哔哩哔哩/哔哩哔哩漫画/掌上英雄联盟/12123 的广告规则)"
# ----------------------------------------------------------------------------


def is_section(line: str) -> bool:
    s = line.strip()
    return s.startswith("[") and s.endswith("]")


def is_boundary(line: str) -> bool:
    s = line.strip()
    return s.startswith("# > ") or s.startswith("# >>>") or is_section(line)


def section_kind(section: str, fallback: str) -> str:
    mapping = {
        "Argument": "argument",
        "Rule": "rule",
        "Rewrite": "rewrite",
        "Script": "script",
        "MITM": "mitm",
    }
    return mapping.get(section, fallback)


def prune(text: str, filename: str, is_plugin: bool):
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)

    raw_url = RAW_URL_BASE + Path(filename).name if is_plugin else None
    desc_re = re.compile(r"^\s*\(已移除[^)]*\)\s*")

    out: list[str] = []
    section = ""
    # 无 [Section] 的规则文件（rejectAd.list）整体按 rule 处理
    fallback = "header" if is_plugin else "rule"
    stats = {"block": 0, "block_names": [], "arg": 0, "mitm": 0, "stray": 0}
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        kind = section_kind(section, fallback)

        if is_section(line):
            section = line.strip()[1:-1].strip()
            out.append(line)
            i += 1
            continue

        # ---- 插件头部：描述标注 + raw-url 改为自己的稳定地址 ----
        if kind == "header" and line.startswith("#!desc="):
            body = line[len("#!desc="):]
            body = desc_re.sub("", body)
            out.append("#!desc=" + DESC_MARKER + body)
            i += 1
            continue
        if kind == "header" and line.startswith("#!raw-url=") and raw_url:
            out.append("#!raw-url=" + raw_url)
            i += 1
            continue

        # ---- [Argument]：删掉 B站 专属开关 ----
        if kind == "argument" and "tag=" in line:
            tag = line.split("tag=", 1)[1]
            if any(tag.lower().startswith(k.lower()) for k in ARG_TAG_KEYWORDS):
                stats["arg"] += 1
                i += 1
                continue

        # ---- 块级删除：# > 哔哩哔哩 / # > 12123 / # > 掌上英雄联盟 ----
        if kind in ("rule", "rewrite", "script") and line.startswith("# > "):
            name = line[4:].strip()
            if any(k in name for k in BLOCK_KEYWORDS):
                stats["block"] += 1
                stats["block_names"].append(f"{section}/{name}")
                i += 1
                while i < n and not is_boundary(lines[i]):
                    i += 1
                while out and out[-1].strip() == "":
                    out.pop()
                out.append("")
                continue

        # ---- [MITM]：按 token 剔除主机名 ----
        if kind == "mitm" and line.strip().startswith("hostname"):
            head = re.match(r"^(\s*hostname\s*=\s*)(.*)$", line, re.IGNORECASE)
            prefix, body = (head.group(1), head.group(2)) if head else ("", line)
            tokens = [t.strip() for t in body.split(",") if t.strip()]
            kept_tokens = [t for t in tokens if t not in DROP_HOSTS]
            stats["mitm"] += len(tokens) - len(kept_tokens)
            out.append(prefix + ", ".join(kept_tokens))
            i += 1
            continue

        # ---- 兜底：非注释正文行仍命中特征则删除（应对上游改名/新增规则）----
        if (
            kind in ("rule", "rewrite", "script", "argument")
            and line.strip()
            and not line.lstrip().startswith("#")
            and DROP_LINE_RE.search(line)
        ):
            stats["stray"] += 1
            i += 1
            continue

        out.append(line)
        i += 1

    # 只在删除位置做空行收敛，不改动文件其余部分的排版
    return nl.join(out), stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Loon 去广告规则自定义裁剪（幂等）")
    ap.add_argument("--src", default=str(ROOT), help="源目录（默认仓库根目录）")
    ap.add_argument("--dst", default=str(ROOT), help="输出目录（默认仓库根目录）")
    ap.add_argument("--in-place", action="store_true", help="就地覆盖源文件")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    args = ap.parse_args()

    src_root, dst_root = Path(args.src), Path(args.dst)
    total = 0
    for src_rel, dst_rel in FILE_MAP.items():
        src = src_root / src_rel
        if not src.exists():
            print(f"[跳过] 找不到 {src}")
            continue
        dst = src if args.in_place else dst_root / dst_rel
        with open(src, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        new_text, st = prune(text, src_rel, is_plugin=src.suffix == ".plugin")
        removed = len(text.splitlines()) - len(new_text.splitlines())
        total += removed
        action = "跳过(未变化)"
        if not args.dry_run:
            if dst.exists():
                with open(dst, "r", encoding="utf-8", newline="") as f:
                    existing = f.read()
            else:
                existing = None
            if existing != new_text:
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(dst, "w", encoding="utf-8", newline="") as f:
                    f.write(new_text)
                action = "已写入"
        print(
            f"{src_rel} -> {dst}: "
            f"相对上游删 {removed} 行 (块 {st['block']}, 开关 {st['arg']}, "
            f"MITM {st['mitm']}, 兜底 {st['stray']}) [{action}]"
        )
        if st["block_names"]:
            print("   块: " + ", ".join(st["block_names"]))

    print("完成。脚本幂等：重复运行生成的文件内容完全一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
