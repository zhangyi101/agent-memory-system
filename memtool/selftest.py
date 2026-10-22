#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memtool.py 自测脚本。只在带时间戳的新 Temp 沙箱目录下操作，不删除任何东西。"""
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8")
    except Exception:
        pass

BUILD_DIR = Path(__file__).resolve().parent
TOOL_SRC = BUILD_DIR / "memtool.py"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
SANDBOX = Path.home().joinpath("AppData", "Local", "Temp", "memtool_sandbox_" + STAMP)

STEPS = []


def run(args, expect_ok=True, cwd=SANDBOX):
    cmd = [sys.executable, str(SANDBOX / "memory_store" / "memtool.py")] + args
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")
    out = (p.stdout or "") + (p.stderr or "")
    ok = (p.returncode == 0) if expect_ok else True
    STEPS.append((("OK" if ok else "FAIL"), " ".join(args), p.returncode, out.strip()))
    print("### memtool.py %s  -> exit=%d" % (" ".join(args), p.returncode))
    print(out.strip())
    print()
    return p.returncode, out


def main():
    SANDBOX.mkdir(parents=True, exist_ok=False)
    (SANDBOX / "memory_store").mkdir(parents=True, exist_ok=True)
    shutil.copy(str(TOOL_SRC), str(SANDBOX / "memory_store" / "memtool.py"))
    (SANDBOX / "config.yaml").write_text(
        "memory:\n  user_char_limit: 8000\n  memory_char_limit: 6000\n", encoding="utf-8")

    run(["init", "--agent", "测试档案"])
    run(["selfcheck"])

    rc, out = run(["add", "--summary", "测试条目", "--content", "用户喜欢热咖啡，默认不加冰",
                   "--tags", "偏好,咖啡", "--importance", "4"])
    rc, out = run(["add", "--summary", "测试条目2", "--content", "用户喜欢热咖啡，默认不加冰",
                   "--tags", "偏好,咖啡", "--importance", "4"])
    assert "DUP" in out, "重复内容应被识别为 DUP，但没有"

    item_json = SANDBOX / "item.json"
    item_json.write_text(json.dumps([{
        "summary": "测试json入库",
        "content": "这是一条通过json批量入库的测试记忆内容，字数稍长一些用于相似度测试",
        "tags": ["测试"], "importance": 3, "status": "draft"}], ensure_ascii=False), encoding="utf-8")
    run(["add", "--json", str(item_json)])

    rc, out = run(["search", "咖啡"])
    assert "咖啡" in out, "搜索应命中咖啡相关条目"

    run(["stats"])
    run(["anchor-add", "--session-id", "sess_001", "--count", "2", "--trigger", "manual"])
    rc, out = run(["anchor-last"])
    assert "sess_001" in out, "anchor-last 应看到刚写入的 session_id"

    rc, out = run(["check", "--show"])
    print("check 返回码（预期非0，因为骨架文件里全是占位符和空分层，这是正常现象）:", rc)

    rc, out = run(["promote", "--id", "3"])
    rc, out = run(["retire", "--id", "1", "--reason", "merged"])
    rc, out = run(["link", "--a", "2", "--b", "3", "--type", "related_to"])
    rc, out = run(["forget", "--id", "1"], expect_ok=False)  # 预期非0：没有 --yes 应被拦截
    assert "STOP" in out, "没有 --yes 时 forget 应该被拦截"
    rc, out = run(["forget", "--id", "1", "--yes"])
    assert "FORGOTTEN" in out

    # 二次 init 应该是幂等的：文件已存在会 skip；bootstrap 记忆里 id=1 刚被 forget 掉，
    # 不会被自动复活（按设计），但也不应该崩溃——这正是要测的边界情况。
    rc, out = run(["init"])
    assert rc == 0, "二次 init 不应崩溃（即使部分 bootstrap 记忆已被 forget）"

    fails = [s for s in STEPS if s[0] == "FAIL"]
    print("=" * 50)
    print("共执行 %d 步，FAIL(exit!=0且未预期) %d 步" % (len(STEPS), len(fails)))
    print("沙箱目录：%s" % SANDBOX)
    print("全部断言通过。" if not fails else "有非预期失败，见上方日志。")


if __name__ == "__main__":
    main()
