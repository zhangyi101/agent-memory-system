#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memtool.py - 记忆系统助手（仅标准库，Python 3.8+）

放置位置: <root>/memory_store/memtool.py   （root 默认 = 本脚本所在目录的上一级）
用法:     python memtool.py <子命令> [参数]
子命令:   init selfcheck add search show retire promote link forget
          anchor-last anchor-add stats check
所有子命令都支持 --root / --db / --config 覆盖默认路径。
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_ROOT = SCRIPT_PATH.parent.parent

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS memories (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  summary      TEXT NOT NULL,
  content      TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  tags         TEXT NOT NULL DEFAULT '',
  emotion      TEXT NOT NULL DEFAULT '',
  importance   INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
  status       TEXT NOT NULL DEFAULT 'final' CHECK (status IN ('draft','final')),
  source       TEXT NOT NULL DEFAULT '',
  session_ref  TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  archived_at  TEXT,
  is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_mem_active  ON memories(is_active, status, importance);
CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created_at);
CREATE TABLE IF NOT EXISTS memory_links (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id_a   INTEGER NOT NULL REFERENCES memories(id),
  memory_id_b   INTEGER NOT NULL REFERENCES memories(id),
  relation_type TEXT NOT NULL DEFAULT 'related_to'
    CHECK (relation_type IN ('related_to','corrects','evolution_of','supplements','co_occurred','aligns_with')),
  note          TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS consolidation_runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  trigger_type    TEXT NOT NULL DEFAULT 'manual' CHECK (trigger_type IN ('cron','manual')),
  last_session_id TEXT NOT NULL DEFAULT '',
  items_ingested  INTEGER NOT NULL DEFAULT 0,
  note            TEXT NOT NULL DEFAULT ''
);
"""

BOOTSTRAP = [
    {
        "summary": "规则·记忆分层",
        "entry": "「规则·记忆分层」我的完整记忆在 memory_store/memory.db；USER.md / MEMORY.md 只是从库里凝练出的精简视图，每条末尾的 @src:ID 指向库中原文。两者不一致时以库为准；要细节先 search 库，不凭印象。",
        "extra": "分层：SOUL=我是谁；USER=用户是谁；MEMORY=环境与经验；skill=怎么做；库=全部记忆的完整底本。工作层满了就从库重新提炼，数据只从库流向工作层。",
    },
    {
        "summary": "铁律·写入纪律",
        "entry": "「铁律·写入纪律」改 USER.md / MEMORY.md / SOUL.md 之前：先备份到 backups/，再把改动对比给用户看，确认后才写，且用文件写入工具直接写盘。无人值守的定时任务只出候选版，不写这三个文件。",
        "extra": "为什么直接写盘：部分平台版本的记忆工具做整文件替换时会覆盖其他条目或被误拦截，已知踩坑；文件写入更可控。",
    },
    {
        "summary": "规则·手动整理",
        "entry": "「规则·手动整理」用户说“整理一下记忆”→ 立刻加载 skill sleep-consolidation，用交互模式执行。对话里遇到被纠正、新偏好、踩坑、重要共识，当场用 memtool add --draft 记一条草稿。",
        "extra": "草稿在下次整理时转正（promote）或丢弃（retire）。触发语可换成与用户约定的说法。",
    },
]

SKILL_TEMPLATE = """---
name: sleep-consolidation
description: "Use when running memory consolidation (cron or user trigger). Ingests new sessions into the memory DB and syncs USER/MEMORY."
version: 1.0.0
---

# 睡眠记忆整理（@@AGENT@@）

## 何时使用
- 定时任务触发（无人值守，cron 模式）
- 用户说出约定的触发语（交互模式）

## 路径（均为绝对路径，别猜）
- 工具: @@TOOL@@
- 数据库: @@DB@@
- USER: @@USER@@
- MEMORY: @@MEMORY@@
- 成长日志: @@GROWTH@@
- 备份目录: @@BACKUPS@@
调用方式: python @@TOOL@@ <子命令>
禁止 python -c、禁止 heredoc 内联。多行或含引号的内容，先用文件写入工具写成 JSON 文件，再 add --json 入库。

## 两种模式
- cron（无人值守）: 不提问，拿不准就记 draft；不写 USER/MEMORY/SOUL，只把候选版和对比放进报告；不执行删除命令（会卡审批），临时文件用写入空内容覆盖；时间有限时按步骤顺序做，但第 9 步必须做。
- 交互（用户在场）: 可以提问；改 USER/MEMORY 前先备份并出对比，用户确认后才写。

## 五把尺（决定要不要记，满足任意一条）
1. 会重复犯的错（被纠正、踩坑、技术限制）
2. 会重复解释的偏好（底线、约定、稳定喜恶）
3. 会改变“我是谁”的修正（规则、架构、成长节点）
4. 会重复排查的技术坑（环境、配置、流程已验证）
5. 会消失的深度共识和灵感

## 执行序列（不可跳步）
1. 运行 date 确认当前时间。
2. python @@TOOL@@ selfcheck。出现 FAIL 就中止，并在报告里写明。
3. python @@TOOL@@ anchor-last，取上次处理到的会话 ID。显示 NO_ANCHOR 表示首次运行：只处理最近 10 个用户会话。
4. 列出锚点之后的全部会话：用平台的完整会话列表命令（Hermes: hermes sessions list），不要只信检索工具默认浏览（只返回最近几场）。排除 cron_ 开头的定时任务会话。
5. 逐场读取会话（读取可能被截断，按可见部分评估），套五把尺；通过的压缩成“学到了什么”，标注情绪，丢掉一次性玩笑、过期状态、临时路径、重复表达、敏感原文、用户说不要记的内容；多条碎片能提炼出洞察的，合成一条。
6. 每条要记的内容：先 search 查重；没有再写 JSON 调 add --json（会话进行中的初步想法加 --draft）。add 报 DUP 就复用已有 ID，不重复入库。
7. python @@TOOL@@ check 体检工作层。无来源或伪标记的条目：库里已有 → 只改 @src 为真实 ID；库里没有 → add 入库后立刻回写 @src（入库和回写必须成对完成）。
8. 本次有 USER/MEMORY 级新增（稳定偏好、关系约定、执行纪律、仍有效的环境信息），或 check 提示超过 90% 容量 → 按手册 3.4 出候选版和对比；cron 模式只写进报告。
9. python @@TOOL@@ anchor-add --session-id <本次处理到的最新会话ID> --count <入库条数> --trigger cron|manual。零入库也必须记录。
10. 看 anchor-add 输出：出现 ALERT（连续两次零入库）就在报告末尾提醒用户检查。
11. 清理本次产生的临时文件。
12. 输出报告（不超过 8 行）：处理会话数、入库条数、check 的 FAIL/WARN 摘要、需要用户确认的事项。

## 红线
- 不存密码、密钥、银行卡、身份证、原始声音样本、用户强情绪下的脆弱原文。
- 用户说“忘掉某件事”：memtool forget --id N --yes 物理删除，并从 USER/MEMORY 删对应条目。
- 一次失败的做法不要原样重试第二遍：停下来查根因，必要时报告用户。
"""


# ---------------------------------------------------------------- 基础工具

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def paths(root):
    root = Path(root).resolve()
    return {
        "root": root,
        "db": root / "memory_store" / "memory.db",
        "tool": root / "memory_store" / "memtool.py",
        "user": root / "memories" / "USER.md",
        "memory": root / "memories" / "MEMORY.md",
        "growth": root / "growth_log.md",
        "backups": root / "backups",
        "skill": root / "skills" / "memory" / "sleep-consolidation" / "SKILL.md",
        "config": root / "config.yaml",
    }


def db_path(a):
    return Path(a.db) if getattr(a, "db", None) else paths(a.root)["db"]


def open_db(a):
    p = db_path(a)
    if not p.exists():
        print("[FAIL] 数据库不存在: %s （先运行 init）" % p)
        sys.exit(2)
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def col_names(con, table):
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]


def norm_text(t):
    return re.sub(r"\s+", " ", t.strip())


def chash(t):
    return hashlib.sha1(norm_text(t).encode("utf-8")).hexdigest()


def bigrams(t):
    t = re.sub(r"[\W_]+", "", t.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def find_limits(config_path):
    out = {}
    try:
        txt = Path(config_path).read_text(encoding="utf-8-sig")
    except Exception:
        return out
    m = re.search(r"(?m)^\s*user_char_limit:\s*(\d+)", txt)
    if m:
        out["user"] = int(m.group(1))
    m = re.search(r"(?m)^\s*memory_char_limit:\s*(\d+)", txt)
    if m:
        out["memory"] = int(m.group(1))
    return out


def write_text(path, text):
    with open(str(path), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def insert_row(con, it):
    cur = con.execute(
        "INSERT INTO memories(summary,content,content_hash,tags,emotion,importance,status,source,session_ref) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (it["summary"], it["content"], chash(it["content"]), it["tags"], it["emotion"],
         it["importance"], it["status"], it["source"], it["session_ref"]))
    return cur.lastrowid


# ---------------------------------------------------------------- init

def cmd_init(a):
    P = paths(a.root)
    for d in (P["db"].parent, P["user"].parent, P["backups"], P["skill"].parent):
        d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(P["db"]))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.commit()
    # 幂等判断：按内容哈希逐条检查，而不是数数量——
    # 数量判断在 bootstrap 条目被用户后续 retire/forget 后会失真，导致二次 init 崩溃或重复插入。
    ids = []
    newly_inserted = 0
    for b in BOOTSTRAP:
        content = b["entry"] + "\n" + b["extra"]
        h = chash(content)
        row = con.execute("SELECT id FROM memories WHERE content_hash=?", (h,)).fetchone()
        if row:
            ids.append(row["id"])
        else:
            new_id = insert_row(con, {
                "summary": b["summary"], "content": content,
                "tags": "记忆体系,bootstrap", "emotion": "认真", "importance": 5,
                "status": "final", "source": "bootstrap", "session_ref": ""})
            ids.append(new_id)
            newly_inserted += 1
    con.commit()
    if newly_inserted:
        print("[OK] 已写入 %d 条初始规则记忆(source=bootstrap)" % newly_inserted)
    else:
        print("[SKIP] 初始规则记忆已存在（若此前被 retire/forget，不会自动恢复，这是预期行为）")
    con.close()
    i1, i2, i3 = ids

    mapline = ("@MAP: 本文件是精简工作层，完整底本=memory_store/memory.db（每条末尾 @src:ID 指向原文）。"
               "仅当条目被截断/用户要求/文件损坏时去查库。")
    prio = "「优先级」铁律 > 规则 > 偏好 > 配置。冲突时按此裁决。"
    ph = "（待填：%s）"
    user_segs = [mapline, prio, "═══ 根基 ═══", ph % "最不能违反的铁律与关系基石，每条带 @src:ID",
                 "═══ 核心 ═══", "═══ 重要 ═══", "═══ 补充 ═══",
                 "═══ 底线重申 ═══", ph % "用最简形式重复三条最核心的铁律"]
    mem_segs = [mapline, prio, "═══ 根基 ═══",
                BOOTSTRAP[1]["entry"] + " @src:%d" % i2,
                "═══ 核心 ═══",
                BOOTSTRAP[0]["entry"] + " @src:%d" % i1,
                BOOTSTRAP[2]["entry"] + " @src:%d" % i3,
                "═══ 重要 ═══", "═══ 补充 ═══",
                "═══ 底线重申 ═══", ph % "用最简形式重复三条最核心的铁律"]
    for key, segs in (("user", user_segs), ("memory", mem_segs)):
        p = P[key]
        if p.exists() and p.read_text(encoding="utf-8-sig").strip():
            print("[SKIP] %s 已存在且非空，保留不动" % p.name)
            if key == "memory":
                print("       请把这三条合并进现有 MEMORY.md（已入库 id=%d,%d,%d）：" % (i1, i2, i3))
                for b, i in ((BOOTSTRAP[0], i1), (BOOTSTRAP[1], i2), (BOOTSTRAP[2], i3)):
                    print("       " + b["entry"] + " @src:%d" % i)
        else:
            write_text(p, "\n§\n".join(segs) + "\n")
            print("[OK] 已创建 %s" % p)
    if not P["growth"].exists():
        write_text(P["growth"], "# 成长日志\n\n过程文档：候选洞察、复盘草稿、待确认想法。已确认的内容写入数据库（memtool add）。\n"
                                "整理锚点不在这里，在数据库 consolidation_runs 表（memtool anchor-last）。\n")
        print("[OK] 已创建 %s" % P["growth"])
    if P["skill"].exists():
        print("[SKIP] skill 已存在: %s" % P["skill"])
    else:
        txt = SKILL_TEMPLATE
        for k, v in (("@@AGENT@@", a.agent or "本档案"), ("@@TOOL@@", SCRIPT_PATH.as_posix()),
                     ("@@DB@@", P["db"].as_posix()), ("@@USER@@", P["user"].as_posix()),
                     ("@@MEMORY@@", P["memory"].as_posix()), ("@@GROWTH@@", P["growth"].as_posix()),
                     ("@@BACKUPS@@", P["backups"].as_posix())):
            txt = txt.replace(k, v)
        write_text(P["skill"], txt)
        print("[OK] 已创建 skill: %s" % P["skill"])
    if SCRIPT_PATH != P["tool"]:
        print("[WARN] 脚本不在 %s ；skill 里的工具路径指向当前位置 %s" % (P["tool"], SCRIPT_PATH))
    return 0


# ---------------------------------------------------------------- selfcheck

def cmd_selfcheck(a):
    P = paths(a.root)
    res = []

    def rec(name, ok, detail="", warn=False):
        res.append(("PASS" if ok else ("WARN" if warn else "FAIL"), name, detail))

    rec("python>=3.8", sys.version_info >= (3, 8), sys.version.split()[0])
    rec("root 存在", P["root"].is_dir(), str(P["root"]))
    dbp = db_path(a)
    rec("数据库文件存在", dbp.exists(), str(dbp))
    if dbp.exists():
        try:
            con = sqlite3.connect(str(dbp))
            tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            need = {"memories", "memory_links", "consolidation_runs"}
            rec("三张表齐全", need <= tabs, "缺: %s" % (need - tabs) if need - tabs else "")
            rec("integrity_check", con.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
            con.close()
            c2 = sqlite3.connect(str(dbp), isolation_level=None)
            try:
                c2.execute("BEGIN IMMEDIATE")
                c2.execute("INSERT INTO consolidation_runs(trigger_type,note) VALUES ('manual','selfcheck')")
                c2.execute("ROLLBACK")
                rec("数据库可写(事务回滚测试)", True)
            except Exception as e:
                rec("数据库可写(事务回滚测试)", False, str(e))
            c2.close()
        except Exception as e:
            rec("数据库可打开", False, str(e))
    for key, label in (("user", "USER.md"), ("memory", "MEMORY.md")):
        p = P[key]
        rec(label + " 存在且可写", p.exists() and os.access(str(p), os.W_OK), str(p))
    rec("backups 目录可写", P["backups"].is_dir() and os.access(str(P["backups"]), os.W_OK), str(P["backups"]))
    rec("sleep-consolidation skill 存在", P["skill"].exists(), str(P["skill"]))
    lim = find_limits(Path(a.config) if a.config else P["config"])
    rec("读到容量上限(config.yaml)", bool(lim.get("user") and lim.get("memory")),
        "user=%s memory=%s" % (lim.get("user"), lim.get("memory")), warn=True)
    bad = 0
    for lv, name, detail in res:
        print("[%s] %s%s" % (lv, name, (" - " + detail) if detail else ""))
        if lv == "FAIL":
            bad += 1
    print("RESULT: %s" % ("FAIL(%d)" % bad if bad else "OK"))
    return 1 if bad else 0


# ---------------------------------------------------------------- add / search / show 等

def cmd_add(a):
    items = []
    if a.json:
        data = json.loads(Path(a.json).read_text(encoding="utf-8-sig"))
        items = data if isinstance(data, list) else [data]
    else:
        if not (a.summary and a.content):
            print("[FAIL] 需要 --json 文件，或同时给 --summary 和 --content")
            return 2
        items = [{"summary": a.summary, "content": a.content, "tags": a.tags, "emotion": a.emotion,
                  "importance": a.importance, "source": a.source, "session_ref": a.session}]
    clean = []
    for i, it in enumerate(items, 1):
        tags = it.get("tags", "")
        if isinstance(tags, list):
            tags = ",".join(str(x) for x in tags)
        try:
            imp = int(it.get("importance", 3))
        except Exception:
            imp = -1
        status = "draft" if (a.draft or it.get("status") == "draft") else "final"
        row = {"summary": str(it.get("summary", "")).strip(), "content": str(it.get("content", "")).strip(),
               "tags": tags, "emotion": str(it.get("emotion", "") or ""), "importance": imp,
               "status": status, "source": str(it.get("source", "") or "manual"),
               "session_ref": str(it.get("session_ref", "") or "")}
        if not row["summary"] or not row["content"]:
            print("[FAIL] 第 %d 条 summary/content 不能为空" % i)
            return 2
        if not 1 <= imp <= 5:
            print("[FAIL] 第 %d 条 importance 必须是 1-5" % i)
            return 2
        clean.append(row)
    con = open_db(a)
    active = con.execute("SELECT id, summary, content FROM memories WHERE is_active=1").fetchall()
    active_bg = [(r["id"], bigrams(r["summary"] + r["content"])) for r in active]
    added = dup = 0
    for row in clean:
        h = chash(row["content"])
        ex = con.execute("SELECT id FROM memories WHERE content_hash=?", (h,)).fetchone()
        if ex:
            print("DUP: 内容与已有记忆完全相同 id=%d，复用该 ID，不重复入库 (%s)" % (ex["id"], row["summary"]))
            dup += 1
            continue
        mine = bigrams(row["summary"] + row["content"])
        for rid, bg in active_bg:
            if mine and bg and len(mine & bg) / float(min(len(mine), len(bg))) >= 0.7:
                print("[WARN] 与 id=%d 高度相似，确认不是同一条（必要时 retire 旧的）" % rid)
        new_id = insert_row(con, row)
        active_bg.append((new_id, mine))
        print("ADDED id=%d status=%s imp=%d | %s" % (new_id, row["status"], row["importance"], row["summary"]))
        added += 1
    con.commit()
    print("RESULT: added=%d duplicates=%d" % (added, dup))
    return 0


def cmd_search(a):
    con = open_db(a)
    terms = [t for t in a.terms if t]
    if not terms:
        print("[FAIL] 至少给一个关键词")
        return 2
    where = " OR ".join(["summary LIKE ? OR content LIKE ? OR tags LIKE ?"] * len(terms))
    params = []
    for t in terms:
        params += ["%" + t + "%"] * 3
    sql = "SELECT * FROM memories WHERE (" + where + ")"
    if not a.all:
        sql += " AND is_active=1"
    rows = con.execute(sql, params).fetchall()
    scored = []
    for r in rows:
        blob = (r["summary"] + " " + r["content"] + " " + r["tags"]).lower()
        scored.append((sum(1 for t in terms if t.lower() in blob), r))
    scored.sort(key=lambda x: (-x[0], -x[1]["importance"], -x[1]["id"]))
    for hit, r in scored[:a.limit]:
        flag = "" if r["is_active"] else " [已退役]"
        print("#%d [%s imp=%d]%s %s | tags=%s" % (r["id"], r["status"], r["importance"], flag, r["summary"], r["tags"]))
        print("    " + r["content"].replace("\n", " ")[:90])
    print("RESULT: %d hit(s)" % len(scored))
    return 0


def cmd_show(a):
    con = open_db(a)
    for i in a.ids:
        r = con.execute("SELECT * FROM memories WHERE id=?", (i,)).fetchone()
        if not r:
            print("#%d 不存在" % i)
            continue
        print("=" * 60)
        for k in r.keys():
            if k != "content_hash":
                print("%s: %s" % (k, r[k]))
    return 0


def cmd_retire(a):
    con = open_db(a)
    r = con.execute("SELECT id, tags FROM memories WHERE id=?", (a.id,)).fetchone()
    if not r:
        print("[FAIL] id=%d 不存在" % a.id)
        return 2
    tags = (r["tags"] + "," if r["tags"] else "") + "retired:" + a.reason
    con.execute("UPDATE memories SET is_active=0, archived_at=?, tags=? WHERE id=?", (now(), tags, a.id))
    con.commit()
    print("RETIRED id=%d reason=%s（原文保留，不再参与同步）" % (a.id, a.reason))
    return 0


def cmd_promote(a):
    con = open_db(a)
    cur = con.execute("UPDATE memories SET status='final' WHERE id=? AND status='draft'", (a.id,))
    con.commit()
    print("PROMOTED id=%d" % a.id if cur.rowcount else "[WARN] id=%d 不是草稿或不存在" % a.id)
    return 0


def cmd_link(a):
    con = open_db(a)
    for i in (a.a, a.b):
        if not con.execute("SELECT 1 FROM memories WHERE id=?", (i,)).fetchone():
            print("[FAIL] id=%d 不存在" % i)
            return 2
    con.execute("INSERT INTO memory_links(memory_id_a,memory_id_b,relation_type,note) VALUES (?,?,?,?)",
                (a.a, a.b, a.type, a.note or ""))
    con.commit()
    print("LINKED %d -[%s]-> %d" % (a.a, a.type, a.b))
    return 0


def cmd_forget(a):
    if not a.yes:
        print("[STOP] forget 是物理删除，不可恢复。仅在用户明确要求“忘掉某件事”时使用；确认后加 --yes。")
        return 2
    con = open_db(a)
    con.execute("DELETE FROM memory_links WHERE memory_id_a=? OR memory_id_b=?", (a.id, a.id))
    cur = con.execute("DELETE FROM memories WHERE id=?", (a.id,))
    con.commit()
    print("FORGOTTEN id=%d（已物理删除；记得同时从 USER/MEMORY 删对应条目）" % a.id if cur.rowcount
          else "[WARN] id=%d 不存在" % a.id)
    return 0


# ---------------------------------------------------------------- 锚点

def zero_alert(con):
    rows = con.execute("SELECT items_ingested FROM consolidation_runs ORDER BY id DESC LIMIT 2").fetchall()
    if len(rows) == 2 and all(r["items_ingested"] == 0 for r in rows):
        print("ALERT: 连续两次整理零入库——确认是真的没有新内容，还是流程在静默失败，并提醒用户。")


def cmd_anchor_last(a):
    con = open_db(a)
    rows = con.execute("SELECT * FROM consolidation_runs ORDER BY id DESC LIMIT 3").fetchall()
    if not rows:
        print("NO_ANCHOR（首次运行：只处理最近 10 个用户会话）")
        return 0
    print("LAST_SESSION_ID=%s" % rows[0]["last_session_id"])
    for r in rows:
        print("run#%d %s [%s] 处理至=%s 入库=%d %s" % (
            r["id"], r["run_at"], r["trigger_type"], r["last_session_id"] or "-", r["items_ingested"], r["note"]))
    zero_alert(con)
    return 0


def cmd_anchor_add(a):
    con = open_db(a)
    if a.count < 0:
        print("[FAIL] --count 不能为负")
        return 2
    prev = con.execute("SELECT last_session_id FROM consolidation_runs ORDER BY id DESC LIMIT 1").fetchone()
    sid = a.session_id if a.session_id is not None else (prev["last_session_id"] if prev else "")
    con.execute("INSERT INTO consolidation_runs(trigger_type,last_session_id,items_ingested,note) VALUES (?,?,?,?)",
                (a.trigger, sid, a.count, a.note or ""))
    con.commit()
    print("ANCHOR_ADDED 处理至=%s 入库=%d trigger=%s" % (sid or "-", a.count, a.trigger))
    zero_alert(con)
    return 0


def cmd_stats(a):
    con = open_db(a)
    cols = col_names(con, "memories")
    total = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    act = con.execute("SELECT COUNT(*) FROM memories WHERE is_active=1").fetchone()[0]
    print("memories: 共 %d 条，有效 %d 条，已退役 %d 条" % (total, act, total - act))
    if "status" in cols:
        print("有效记忆按状态: %s" % dict(con.execute(
            "SELECT status, COUNT(*) FROM memories WHERE is_active=1 GROUP BY status").fetchall()))
        cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        old = con.execute("SELECT COUNT(*) FROM memories WHERE is_active=1 AND status='draft' AND created_at<?",
                          (cutoff,)).fetchone()[0]
        if old:
            print("[WARN] 有 %d 条草稿超过 14 天未转正/退役，下次整理时处理" % old)
    print("有效记忆按重要度: %s" % dict(con.execute(
        "SELECT importance, COUNT(*) FROM memories WHERE is_active=1 GROUP BY importance ORDER BY importance").fetchall()))
    cmd_anchor_last(a)
    return 0


# ---------------------------------------------------------------- check（工作层体检）

TAG_RE = re.compile(r"^「(铁律|规则|偏好|配置)[^」]*」")
MARK_RE = re.compile(r"@src:(\S+)")
HDR_RE = re.compile(r"^═+\s*(.+?)\s*═+$")


def check_file(label, path, limit, con, cnt, show):
    def emit(level, where, msg):
        cnt[level] += 1
        print("[%s] %s%s: %s" % (level, label, (" " + where) if where else "", msg))

    if not path.exists():
        emit("FAIL", "", "文件不存在: %s" % path)
        return
    text = path.read_text(encoding="utf-8-sig")
    n = len(text)
    if limit:
        pct = n * 100.0 / limit
        msg = "字符数 %d / 上限 %d = %.0f%%" % (n, limit, pct)
        if pct > 100:
            emit("FAIL", "", msg + "，已超限")
        elif pct > 90:
            emit("WARN", "", msg + "，超过 90%，应触发重写（手册 3.6）")
        else:
            print("[OK] %s %s" % (label, msg))
    else:
        emit("WARN", "", "未读到容量上限，跳过容量检查（用 --config 指向 config.yaml）")
    segs = [s.strip() for s in re.split(r"(?m)^§\s*$", text) if s.strip()]
    has_map = has_prio = False
    headers = []
    entries = 0
    for idx, s in enumerate(segs, 1):
        where = "#%d" % idx
        if s.startswith("@MAP"):
            has_map = True
            if idx != 1:
                emit("WARN", where, "@MAP 映射行应是第一条")
            continue
        if s.startswith("「优先级」"):
            has_prio = True
            continue
        m = HDR_RE.match(s)
        if m:
            headers.append(m.group(1))
            continue
        if s.startswith("（待填"):
            emit("WARN", where, "占位符尚未填写")
            continue
        entries += 1
        prev = s.replace("\n", " ")[:24]
        issues = []
        tag_lines = [ln for ln in s.split("\n") if TAG_RE.match(ln.strip())]
        if len(tag_lines) > 1:
            issues.append(("WARN", "一段里有多条「标签」，疑似缺少 § 分隔"))
        if not TAG_RE.match(s):
            issues.append(("WARN", "缺少「铁律/规则/偏好/配置·名称」标签"))
        marks = MARK_RE.findall(s)
        body = MARK_RE.sub("", s)
        body = TAG_RE.sub("", body)
        refs = []
        if not marks:
            issues.append(("FAIL", "无 @src 来源标记"))
        else:
            tok = re.sub(r"\[bg\]$", "", marks[-1])
            if tok == "?":
                issues.append(("WARN", "来源待追溯 (@src:?)"))
            elif re.match(r"\d{4}-\d{2}-\d{2}", tok):
                issues.append(("FAIL", "日期型伪标记 @src:%s（功能上等于无来源）" % tok))
            elif re.fullmatch(r"\d+(,\d+)*", tok):
                refs = [int(x) for x in tok.split(",")]
            else:
                issues.append(("FAIL", "非法 @src 格式: %s" % tok))
        if refs:
            if con is None:
                issues.append(("FAIL", "数据库不可用，无法核对 @src"))
            else:
                texts = []
                for rid in refs:
                    r = con.execute("SELECT * FROM memories WHERE id=?", (rid,)).fetchone()
                    if not r:
                        issues.append(("FAIL", "@src:%d 在库中不存在" % rid))
                        continue
                    ks = r.keys()
                    if "is_active" in ks and not r["is_active"]:
                        issues.append(("WARN", "@src:%d 已退役，条目可能过期" % rid))
                    if "status" in ks and r["status"] == "draft":
                        issues.append(("WARN", "@src:%d 仍是草稿，未转正" % rid))
                    texts.append(r["summary"] + " " + r["content"])
                    if show:
                        print("      -> #%d %s" % (rid, r["summary"]))
                if texts:
                    eb = bigrams(body)
                    rb = bigrams(" ".join(texts))
                    if len(eb) >= 10 and len(eb & rb) / float(len(eb)) < 0.12:
                        issues.append(("WARN", "与 @src 指向的原文几乎无重叠，疑似指错（格式合法但内容不符）"))
        if re.search(r"该用户|用户偏好", body):
            issues.append(("WARN", "档案腔/第三人称（应用第一人称备忘录体）"))
        if re.search(r"20\d\d-\d\d-\d\d", body):
            issues.append(("WARN", "含具体日期，可能是会过时的信息"))
        for lv, msg in issues:
            emit(lv, where + " " + prev, msg)
    if not has_map:
        emit("WARN", "", "缺少 @MAP 映射行")
    if not has_prio:
        emit("WARN", "", "缺少「优先级」声明行")
    if "底线重申" not in headers:
        emit("WARN", "", "缺少 底线重申 区")
    print("[OK] %s 条目数=%d 分层标题=%s" % (label, entries, "/".join(headers)))


def cmd_check(a):
    P = paths(a.root)
    cfg = Path(a.config) if a.config else P["config"]
    lim = find_limits(cfg)
    if a.user_limit:
        lim["user"] = a.user_limit
    if a.memory_limit:
        lim["memory"] = a.memory_limit
    dbp = db_path(a)
    con = None
    cnt = {"FAIL": 0, "WARN": 0}
    if dbp.exists():
        con = sqlite3.connect(str(dbp))
        con.row_factory = sqlite3.Row
        ic = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            cnt["FAIL"] += 1
            print("[FAIL] 数据库 integrity_check: %s" % ic)
    else:
        cnt["FAIL"] += 1
        print("[FAIL] 数据库不存在: %s" % dbp)
    check_file("USER.md", Path(a.user_file) if a.user_file else P["user"], lim.get("user"), con, cnt, a.show)
    check_file("MEMORY.md", Path(a.memory_file) if a.memory_file else P["memory"], lim.get("memory"), con, cnt, a.show)
    print("RESULT: FAIL=%d WARN=%d" % (cnt["FAIL"], cnt["WARN"]))
    return 1 if cnt["FAIL"] else 0


# ---------------------------------------------------------------- 入口

def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=str(DEFAULT_ROOT), help="档案根目录（默认=脚本上级目录）")
    common.add_argument("--db", help="覆盖数据库路径")
    common.add_argument("--config", help="覆盖 config.yaml 路径")
    ap = argparse.ArgumentParser(prog="memtool.py", description="记忆系统助手")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def mk(name, fn, help_):
        p = sub.add_parser(name, parents=[common], help=help_)
        p.set_defaults(fn=fn)
        return p

    p = mk("init", cmd_init, "建目录/建库/写骨架/生成 skill（可重复运行，不覆盖已有文件）")
    p.add_argument("--agent", help="档案名称，写进 skill 标题")
    mk("selfcheck", cmd_selfcheck, "环境自检")
    p = mk("add", cmd_add, "入库（优先 --json 文件）")
    p.add_argument("--json")
    p.add_argument("--summary")
    p.add_argument("--content")
    p.add_argument("--tags", default="")
    p.add_argument("--emotion", default="")
    p.add_argument("--importance", type=int, default=3)
    p.add_argument("--source", default="manual")
    p.add_argument("--session", default="")
    p.add_argument("--draft", action="store_true", help="作为草稿入库")
    p = mk("search", cmd_search, "按关键词查库（OR 语义，按命中数排序）")
    p.add_argument("terms", nargs="+")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--all", action="store_true", help="包含已退役记忆")
    p = mk("show", cmd_show, "显示完整记录")
    p.add_argument("ids", nargs="+", type=int)
    p = mk("retire", cmd_retire, "退役（合并/过期/写错），原文保留")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--reason", choices=["merged", "obsolete", "wrong"], default="merged")
    p = mk("promote", cmd_promote, "草稿转正")
    p.add_argument("--id", type=int, required=True)
    p = mk("link", cmd_link, "建立两条记忆的关联")
    p.add_argument("--a", type=int, required=True)
    p.add_argument("--b", type=int, required=True)
    p.add_argument("--type", default="related_to",
                   choices=["related_to", "corrects", "evolution_of", "supplements", "co_occurred", "aligns_with"])
    p.add_argument("--note", default="")
    p = mk("forget", cmd_forget, "物理删除（仅限用户明确要求忘掉）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--yes", action="store_true")
    mk("anchor-last", cmd_anchor_last, "查看上次整理锚点")
    p = mk("anchor-add", cmd_anchor_add, "记录本次整理锚点（零入库也要记）")
    p.add_argument("--session-id", default=None)
    p.add_argument("--count", type=int, default=0)
    p.add_argument("--trigger", choices=["cron", "manual"], default="manual")
    p.add_argument("--note", default="")
    mk("stats", cmd_stats, "统计")
    p = mk("check", cmd_check, "工作层体检（来源标记/容量/格式/疑似指错）")
    p.add_argument("--show", action="store_true", help="显示每条 @src 指向的摘要，便于人工抽查")
    p.add_argument("--user-file")
    p.add_argument("--memory-file")
    p.add_argument("--user-limit", type=int)
    p.add_argument("--memory-limit", type=int)

    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
