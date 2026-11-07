#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
custom/loon 定制插件守卫校验器

给 GitHub Actions 和本地手动同步共同使用。任何一项不通过就返回退出码 1，
自动同步流程会立刻中止（此时 push 尚未执行，远程 main 不会发生任何变化）。

校验内容
  1. 裁剪脚本 / 配置仍存在，没有被上游改写
  2. 三个生成物存在、行数正常、确实比上游源文件少（证明裁剪生效）
  3. 正文中不含 B站 / 掌上英雄联盟 / 12123 的任何特征（#! 头部元数据除外）
  4. 插件 #!desc 带定制标注、#!raw-url 指向本 fork 的 custom/loon/ 路径
  5. [Argument] / [Rule] / [Rewrite] / [Script] / [MITM] 段完整
  6. [MITM] 不含被排除的 10 个主机名，且 us.l.qq.com 仍在（防止误删其它应用）

用法：
  python custom/verify_custom.py
  python custom/verify_custom.py --baseline /tmp/custom-guard/custom/loon   # Actions 里额外比对上一版
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "custom" / "loon"

# 正文里出现这些特征 = 裁剪被覆盖 / 漏删
BANNED = re.compile(
    r"bilibili|biliapi\.net|hdslb\.com|biligame\.com"
    r"|mlol\.qt\.qq\.com|ossweb-img\.qq\.com|gab\.122\.gov\.cn"
    r"|12123|英雄联盟|哔哩",
    re.IGNORECASE,
)

DROP_HOSTS = {
    "grpc.biliapi.net",
    "app.bilibili.com",
    "api.bilibili.com",
    "api.live.bilibili.com",
    "line3-h5-mobile-api.biligame.com",
    "manga.bilibili.com",
    "i*.hdslb.com",
    "mlol.qt.qq.com",
    "ossweb-img.qq.com",
    "gab.122.gov.cn",
}

MUST_KEEP_HOST = "us.l.qq.com"
RAW_URL_PREFIX = (
    "https://raw.githubusercontent.com/zheng-feifei/wool_scripts/main/custom/loon/"
)

# 生成物 -> (上游源文件, 最低行数)
TARGETS = {
    "blockAds.plugin": ("Loon/plugin/blockAds.plugin", 7000),
    "blockAdsV2.plugin": ("Loon/plugin/blockAdsV2.plugin", 7000),
    "rejectAd.list": ("Loon/rule/rejectAd.list", 3000),
}

SECTIONS = ("[Argument]", "[Rule]", "[Rewrite]", "[Script]", "[MITM]")

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def read(path: Path) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def mitm_hosts(text: str) -> list[str]:
    """取最后一个真正的 [MITM] 段（V2 头部注释里也含 [MITM] 字样，需跳过）"""
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.strip() == "[MITM]"]
    if not idx:
        return []
    body = "\n".join(lines[idx[-1] + 1:]).replace("hostname =", "")
    return [t.strip() for t in body.split(",") if t.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="校验 custom/loon 定制插件完整性")
    ap.add_argument(
        "--baseline",
        help="同步前的 custom/loon 目录；用于检测行数异常波动（上游格式变化导致误裁）",
    )
    args = ap.parse_args()
    baseline = Path(args.baseline) if args.baseline else None

    # 1. 裁剪逻辑本身不能被上游覆盖
    for name in ("prune_ads.py", "verify_custom.py"):
        if not (ROOT / "custom" / name).is_file():
            fail(f"裁剪脚本缺失：custom/{name}（可能被上游覆盖）")

    for out_name, (src_rel, min_lines) in TARGETS.items():
        out_path = OUT_DIR / out_name
        src_path = ROOT / src_rel
        if not out_path.is_file():
            fail(f"定制文件缺失：custom/loon/{out_name}")
            continue
        if not src_path.is_file():
            fail(f"上游源文件缺失：{src_rel}")
            continue

        text = read(out_path)
        lines = text.splitlines()
        src_lines = len(read(src_path).splitlines())

        # 2. 文件规模 & 确实裁剪过
        if len(lines) < min_lines:
            fail(f"{out_name} 只有 {len(lines)} 行（下限 {min_lines}），疑似被覆盖或裁空")
        if len(lines) >= src_lines:
            fail(f"{out_name} 行数 {len(lines)} 未少于上游源文件 {src_lines}，裁剪未生效")
        print(
            f"  {out_name}: {len(lines)} 行，相对上游减少 {src_lines - len(lines)} 行"
        )

        # 2b. 相对上一版的行数波动（上游格式大改时防止误裁/整段被吃掉）
        if baseline and (baseline / out_name).is_file():
            before = len(read(baseline / out_name).splitlines())
            if before > 0:
                ratio = len(lines) / before
                if ratio < 0.8 or ratio > 1.5:
                    fail(
                        f"{out_name} 行数相对上一版 {before} -> {len(lines)}"
                        f"（{ratio:.0%}），疑似上游格式变化导致裁剪异常"
                    )

        # 3. 正文禁用特征（跳过 #! 头部元数据行；块头注释同样要查）
        hits = []
        for i, ln in enumerate(lines, 1):
            if ln.lstrip().startswith("#!"):
                continue
            if BANNED.search(ln):
                hits.append(f"L{i}: {ln[:110]}")
        if hits:
            fail(f"{out_name} 仍含被排除应用的规则（{len(hits)} 处）：")
            errors.extend("    " + h for h in hits[:5])

        # 4. 插件头部：定制标注 + raw-url 指向自己的稳定地址
        if out_name.endswith(".plugin"):
            desc = next((ln for ln in lines if ln.startswith("#!desc=")), "")
            if "(已移除" not in desc:
                fail(f"{out_name} 缺少 #!desc 定制标注，疑似被上游原版覆盖")
            raw = next((ln for ln in lines if ln.startswith("#!raw-url=")), "")
            if not raw.startswith("#!raw-url=" + RAW_URL_PREFIX):
                fail(
                    f"{out_name} 的 #!raw-url 不是自有 custom 路径：{raw[:120]}"
                    "（Loon 内更新插件会拉回上游原版）"
                )

            # 5. 段结构完整
            for sec in SECTIONS:
                if not any(ln.strip() == sec for ln in lines):
                    fail(f"{out_name} 缺少段 {sec}")

            # 6. MITM 主机名
            hosts = mitm_hosts(text)
            if len(hosts) < 900:
                fail(f"{out_name} 的 MITM 只剩 {len(hosts)} 个主机名，疑似被清空")
            leaked = sorted(DROP_HOSTS & set(hosts))
            if leaked:
                fail(f"{out_name} 的 MITM 仍含被排除主机名：{leaked}")
            if MUST_KEEP_HOST not in hosts:
                fail(f"{out_name} 的 MITM 丢了 {MUST_KEEP_HOST}（QQ音乐等会受影响）")
            print(f"    MITM 共 {len(hosts)} 个主机名（排除项/保留项已在下方校验）")

    print()
    if errors:
        print("校验失败：")
        for e in errors:
            print("  - " + e)
        print("\n自动同步流程将中止，远程 main 不会被改动。")
        return 1
    print("校验通过：定制插件完整，未被上游覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
