#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""理解层配置版本管理 CLI。

用法：
  python -m scripts.config_version list                # 版本历史
  python -m scripts.config_version snapshot --label L  # 手动快照当前 live
  python -m scripts.config_version show N              # 查看某版本内容
  python -m scripts.config_version rollback N          # 回滚到版本 N（回滚前自动快照）
  python -m scripts.config_version rollback --last     # 回滚到上一版本
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from scripts import config_versioning as cv

GRAY_JSON = os.path.join(cv._CONFIG_DIR, "gray.json")


def _read_gray() -> dict:
    if not os.path.exists(GRAY_JSON):
        return {}
    try:
        with open(GRAY_JSON, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


def _write_gray(g: dict) -> None:
    with open(GRAY_JSON, "w", encoding="utf-8") as f:
        json.dump(g, f, ensure_ascii=False, indent=2)


def _cmd_gray(args) -> None:
    if args.gray_cmd == "start":
        # 校验版本快照存在
        try:
            cv.show(args.version)
        except FileNotFoundError as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)
        if not (0 <= args.percent <= 100):
            print("percent 需在 0-100。")
            sys.exit(2)
        file = os.path.abspath(cv._version_file(args.version))
        _write_gray({"active": True, "version": args.version, "percent": args.percent, "file": file})
        print(f"灰度已启动：{args.percent}% 流量走版本 v{args.version}（live 仍为 v{cv.current_version()}）。")
        print(f"观察后可 promote（放量）或 abort（放弃）。control: {GRAY_JSON}")

    elif args.gray_cmd == "status":
        g = _read_gray()
        live = cv.current_version()
        if g.get("active"):
            print(f"灰度中：{g.get('percent')}% 走 v{g.get('version')}（live=v{live}）")
            print(f"control: {GRAY_JSON} → file: {g.get('file')}")
        else:
            print(f"未灰度（live=v{live}）。用 `gray start <version> --percent P` 启动。")

    elif args.gray_cmd == "promote":
        g = _read_gray()
        if not g.get("active"):
            print("当前未灰度，无需 promote。")
            return
        ver = g.get("version")
        try:
            cur = cv.rollback_to(ver)  # 把灰度量版本复制为 live（回滚前自动快照）
        except (ValueError, FileNotFoundError) as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)
        _write_gray({"active": False, "version": ver, "percent": 0, "file": ""})
        print(f"已 promote：v{ver} 成为 live（current=v{cur}），灰度关闭。")
        print("建议随后跑 test 回归：python -m scripts.check_intent_regression")

    elif args.gray_cmd == "abort":
        if not _read_gray().get("active"):
            print("当前未灰度。")
            return
        _write_gray({"active": False, "version": None, "percent": 0, "file": ""})
        print(f"已中止灰度，live 保持 v{cv.current_version()}。")


def _print_manifest(verbose: bool = False) -> None:
    versions = cv.list_versions()
    cur = cv.current_version()
    print(f"当前 live = 版本 {cur}；共 {len(versions)} 个版本（快照不可变）")
    if not versions:
        print("（尚无版本。第一次 --apply 或 snapshot 会自动建。live 当前为空 few_shot 配置。）" if cv.live_is_clean() else "（尚无版本，请先 snapshot。）")
    for v in versions:
        marker = "*" if v["version"] == cur else " "
        line = f"{marker} v{v['version']}  {v['created_at']}  {v['label']}"
        if verbose and v.get("changes"):
            line += "  [" + "; ".join(v["changes"]) + "]"
        print(line)


def main() -> None:
    ap = argparse.ArgumentParser(description="理解层配置版本管理与回滚")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="版本历史")
    sp = sub.add_parser("snapshot", help="快照当前 live 配置")
    sp.add_argument("--label", default="")
    sp.add_argument("--changes", action="append", default=[])

    sh = sub.add_parser("show", help="查看某版本内容")
    sh.add_argument("version", type=int)

    rb = sub.add_parser("rollback", help="回滚到某版本（或 --last）")
    rb.add_argument("version", type=int, nargs="?")
    rb.add_argument("--last", action="store_true", help="回滚到 current-1（上一版本）")

    gr = sub.add_parser("gray", help="灰度发布（start/status/promote/abort）")
    gr.add_argument("gray_cmd", choices=["start", "status", "promote", "abort"])
    gr.add_argument("version", type=int, nargs="?")
    gr.add_argument("--percent", type=float, default=10, help="放量百分比 0-100（start 用）")

    a = ap.parse_args()

    if a.cmd == "gray":
        _cmd_gray(a)
        return
    if a.cmd == "list":
        _print_manifest(verbose=True)
    elif a.cmd == "snapshot":
        v = cv.snapshot(label=a.label or "manual", changes=a.changes)
        print(f"已快照 live → 版本 v{v}")
    elif a.cmd == "show":
        try:
            print(cv.show(a.version))
        except FileNotFoundError as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)
    elif a.cmd == "rollback":
        if a.last:
            cur = cv.current_version()
            versions = [v["version"] for v in cv.list_versions()]
            older = [x for x in versions if x < cur]
            if not older:
                print("无更早版本可回滚。")
                sys.exit(1)
            target = max(older)
        elif a.version is None:
            print("rollback 需指定版本号，或加 --last。")
            sys.exit(2)
        else:
            target = a.version
        try:
            cur = cv.rollback_to(target)
            print(f"已回滚到版本 v{target}（当前 live = v{cur}）。理解层运行时读取，下次调用生效。")
        except (ValueError, FileNotFoundError) as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
