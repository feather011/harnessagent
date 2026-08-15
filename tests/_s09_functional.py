#!/usr/bin/env python3
"""s09 函数级验证（临时脚本，mock 模型调用 + 临时目录，不碰真实 .memory/ 与 API）。"""
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import s09_memory as M


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


def fake_create(payload=None):
    """返回一个假 create 函数；payload 为要注入的响应文本（可含 placeholder）。"""
    def _create(**kwargs):
        text = payload or "[]"
        # 允许 payload 是 callable，根据 messages 内容定制响应
        if callable(payload):
            text = payload(kwargs.get("messages", []))
        return _Resp(text)
    return _create


def new_store():
    # memory_path 强制 memory 目录在 WORKDIR 下（防越界设计），故用 WORKDIR 内临时目录
    tmp = M.WORKDIR / f".tmp_s09_{uuid.uuid4().hex[:8]}"
    store = M.MemoryStore(tmp, M.client, M.MODEL)
    return store, tmp


PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


# ---------------- Storage ----------------
store, tmp = new_store()

p = store.write_memory_file("user-preference-tabs", "user", "User prefers tabs for indentation",
                            "User prefers using tabs, not spaces, for indentation.")
check("write_memory_file 落盘", p.name == "user-preference-tabs.md" and p.is_file())
index = store.read_memory_index()
check("索引更新含该条", "user-preference-tabs.md" in index and "tabs" in index)
check("frontmatter 正确", store.parse_frontmatter(p.read_text("utf-8"))[0].get("type") == "user")
recs = store.list_memory_files()
check("list_memory_files 解析", len(recs) == 1 and recs[0]["name"] == "user-preference-tabs")

# memory_path 安全校验
for bad in ("../evil.md", "sub/x.md"):
    try:
        store.memory_path(bad)
        check(f"memory_path 拒绝 {bad}", False)
    except ValueError:
        check(f"memory_path 拒绝 {bad}", True)
try:
    store.memory_path("MEMORY.md")
    check("memory_path 拒绝索引", False)
except ValueError:
    check("memory_path 拒绝索引", True)
check("memory_path 允许索引(allow_index)", store.memory_path("MEMORY.md", allow_index=True).name == "MEMORY.md")

# 覆盖同名（slug 相同）
store.write_memory_file("User-Preference-Tabs", "user", "updated desc", "updated body")
check("同名 slug 覆盖", store.list_memory_files()[0]["body"] == "updated body")

# 中文 slug
store.write_memory_file("中文记忆", "project", "中文描述", "中文正文")
check("中文 slug 可写", (tmp / "中文记忆.md").exists())

# ---------------- should_store_memory ----------------
base = {"name": "feedback-lint", "type": "feedback", "scope": "persistent",
        "description": "Do not mock the database", "body": "Always use real DB in tests"}
check("合法 persistent 通过", store.should_store_memory(dict(base), []) is True)
check("scope=current_task 拒绝", store.should_store_memory({**base, "scope": "current_task"}, []) is False)
check("scope 缺失 拒绝", store.should_store_memory({k: v for k, v in base.items() if k != "scope"}, []) is False)
check("type 非法 拒绝", store.should_store_memory({**base, "type": "junk"}, []) is False)
check("body 缺失 拒绝", store.should_store_memory({**base, "body": "  "}, []) is False)
check("英文临时标记 拒绝", store.should_store_memory({**base, "body": "only for this session"}, []) is False)
check("中文临时标记 拒绝", store.should_store_memory({**base, "body": "本次任务临时限制"}, []) is False)
check("非 dict 拒绝", store.should_store_memory("x", []) is False)
check("同名重复 拒绝", store.should_store_memory(dict(base), [{"name": "feedback-lint"}]) is False)
check("同描述重复 拒绝", store.should_store_memory(dict(base), [{"name": "other", "description": "Do not mock the database"}]) is False)
check("同 body 重复 拒绝", store.should_store_memory(dict(base), [{"name": "other", "body": "Always use real DB in tests"}]) is False)

# ---------------- validate_memory_record ----------------
check("require_scope 缺 scope 拒绝", store.validate_memory_record({"name": "a", "type": "user", "description": "d", "body": "b"}, require_scope=True) is None)
check("require_scope current_task 通过", store.validate_memory_record({**base, "scope": "current_task"}, require_scope=True)["scope"] == "current_task")
check("不 require_scope 缺 scope 通过", store.validate_memory_record({"name": "a", "type": "user", "description": "d", "body": "b"}) is not None)

# ---------------- extract_json_array ----------------
check("extract_json_array 纯数组", M.MemoryStore.extract_json_array("[0, 2]") == [0, 2])
check("extract_json_array 带前缀文本", M.MemoryStore.extract_json_array('Here: [{"name":"a"}]') == [{"name": "a"}])
check("extract_json_array 无数组", M.MemoryStore.extract_json_array("no array here") == [])

# ---------------- Recall: select (模型成功 + fallback) ----------------
rec_store, rtmp = new_store()
rec_store.write_memory_file("pref-tabs", "user", "Tabs for indentation", "Use tabs.")
rec_store.write_memory_file("pref-spaces", "user", "Spaces for indentation", "Use spaces.")
messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "What indentation do I prefer?"}]

rec_store.client.chat.completions.create = fake_create("[1]")  # 排序后 pref-spaces.md=0, pref-tabs.md=1
sel = rec_store.select_relevant_memories(messages)
check("select 模型命中索引", sel == ["pref-tabs.md"], f"sel={sel}")

rec_store.client.chat.completions.create = fake_create(payload=lambda msgs: (_ for _ in ()).throw(RuntimeError("boom")))
sel2 = rec_store.select_relevant_memories([{"role": "user", "content": "Which tab style do I use"}])
check("select 模型失败→keyword fallback", sel2 == ["pref-tabs.md"], f"sel2={sel2}")

# load_memories 单条限 2000
big_body = "x" * 5000
rec_store.write_memory_file("big-record", "project", "A very large record", big_body)
rec_store.client.chat.completions.create = fake_create("[0]")  # 排序后 big-record.md=0
lm = json.loads(rec_store.load_memories(messages))
check("load_memories 单条截断≤2000", len(lm[0]["content"]) == 2000, f"len={len(lm[0]['content'])}")

# load_memory 工具：命中 / 未命中
check("load_memory 命中返回全文", "Use tabs." in rec_store.load_memory("pref-tabs"))
check("load_memory slug 匹配", "Use tabs." in rec_store.load_memory("pref-tabs.md") or "Use tabs." in rec_store.load_memory("pref tabs"))
check("load_memory 未命中 Available", "Unknown memory" in rec_store.load_memory("nope") and "pref-tabs" in rec_store.load_memory("nope"))

# recent_user_text 跳过 tool 结果
msgs2 = [{"role": "user", "content": "task"}, {"role": "assistant", "content": "", "tool_calls": [{}]},
         {"role": "tool", "content": "huge tool output"}, {"role": "assistant", "content": "reply"}]
check("recent_user_text 跳过 tool 结果", "huge tool output" not in rec_store.recent_user_text(msgs2))

# ---------------- Extraction ----------------
ex_store, etmp = new_store()
ex_store.client.chat.completions.create = fake_create(json.dumps([
    {"name": "pref-tabs", "type": "user", "scope": "persistent",
     "description": "User prefers tabs", "body": "User prefers using tabs for indentation."},
    {"name": "temp-path", "type": "project", "scope": "current_task",
     "description": "Temp path", "body": "Use /tmp/x for this task."},
    {"name": "junk", "type": "junk", "scope": "persistent", "description": "x", "body": "y"},
]))
dialogue = [{"role": "user", "content": "I prefer tabs. Remember that."},
            {"role": "assistant", "content": "Noted."}]
n = ex_store.extract_memories(dialogue)
check("extract 只存 persistent 合法候选", n == 1, f"stored={n}")
check("temp-path 未落盘", not (etmp / "temp-path.md").exists())
check("junk 未落盘", not (etmp / "junk.md").exists())
check("pref-tabs 落盘", (etmp / "pref-tabs.md").exists())

# extract 模型调用异常 → 返回 0 不崩
ex_store.client.chat.completions.create = fake_create(payload=lambda msgs: (_ for _ in ()).throw(RuntimeError("boom")))
check("extract 异常返回 0", ex_store.extract_memories(dialogue) == 0)

# ---------------- Consolidation ----------------
con_store, ctmp = new_store()
for i in range(9):
    con_store.write_memory_file(f"m{i}", "project", f"desc {i}", f"body {i}")
check("consolidate <10 返回 0", con_store.consolidate_memories() == 0)

con_store.write_memory_file("m9", "project", "desc 9", "body 9")
check("10 条 记录数", len(con_store.list_memory_files()) == 10)

# 合并成功：mock 返回 4 条合并结果
con_store.client.chat.completions.create = fake_create(json.dumps([
    {"name": "merged-a", "type": "project", "description": "merged A", "body": "merged body A"},
    {"name": "merged-b", "type": "project", "description": "merged B", "body": "merged body B"},
    {"name": "merged-c", "type": "project", "description": "merged C", "body": "merged body C"},
    {"name": "merged-d", "type": "project", "description": "merged D", "body": "merged body D"},
]))
n = con_store.consolidate_memories()
check("consolidate 合并成功", n == 4, f"n={n}")
check("consolidate 后目录只有 4+索引", sorted(f.name for f in ctmp.glob("*.md")) ==
      sorted(["MEMORY.md", "merged-a.md", "merged-b.md", "merged-c.md", "merged-d.md"]))

# 合并失败（模型返回空）→ 不丢历史
con_store2, ctmp2 = new_store()
for i in range(10):
    con_store2.write_memory_file(f"n{i}", "project", f"desc {i}", f"body {i}")
con_store2.client.chat.completions.create = fake_create("not a json array")
check("consolidate 空结果→0 不丢", con_store2.consolidate_memories() == 0
      and len(con_store2.list_memory_files()) == 10)

# 合并写入失败 → snapshot 回滚
con_store3, ctmp3 = new_store()
for i in range(10):
    con_store3.write_memory_file(f"o{i}", "project", f"desc {i}", f"body {i}")
con_store3.client.chat.completions.create = fake_create(json.dumps([
    {"name": "ok-one", "type": "project", "description": "ok", "body": "ok body"},
    {"name": "Bad Target", "type": "project", "description": "bad", "body": "bad body"},
]))
orig_path = con_store3.memory_path
def failing_path(filename, allow_index=False):
    p = orig_path(filename, allow_index)
    if filename == "bad-target.md":
        raise OSError("simulated write failure")
    return p
con_store3.memory_path = failing_path
check("consolidate 写入失败→回滚", con_store3.consolidate_memories() == 0
      and len(con_store3.list_memory_files()) == 10
      and all(r["body"] == f"body {i}" for i, r in enumerate(con_store3.list_memory_files())))

# ---------------- ContextCompactor 保留 system ----------------
sys_msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "u1"}]
compacted = [{"role": "user", "content": "[Compacted]..."}, {"role": "user", "content": "u2"}]
out = M.ContextCompactor._with_system(sys_msgs, compacted)
check("_with_system 保留 system 在最前", out[0]["role"] == "system" and out[1:] == compacted)
check("_with_system 无 system 原样", M.ContextCompactor._with_system([{"role": "user", "content": "u"}], compacted)[0]["role"] == "user")

# ---------------- 工具绑定 ----------------
check("TOOLS 含 load_memory", any(t.get("function", {}).get("name") == "load_memory" for t in M.TOOLS))
check("TOOL_HANDLERS 含 load_memory", "load_memory" in M.TOOL_HANDLERS)
check("load_memory 可调用", callable(M.TOOL_HANDLERS["load_memory"]))

# build_system_prompt：有 memory 索引时含 catalog + 引导；无索引时不加段
orig_index = M.MEMORY.read_memory_index
M.MEMORY.read_memory_index = lambda: "- [pref-tabs](pref-tabs.md) - User prefers tabs"
prompt = M.build_system_prompt()
check("build_system_prompt 含引导", "Use load_memory" in prompt and "pref-tabs" in prompt
      and "current user request takes priority" in prompt)
M.MEMORY.read_memory_index = orig_index
check("build_system_prompt 无索引不加段", "Use load_memory" not in M.build_system_prompt())

for t in tmp, rtmp, etmp, ctmp, ctmp2, ctmp3:
    shutil.rmtree(t, ignore_errors=True)

print(f"\n=== s09 函数级 === 通过 {len(PASSED)} | 失败 {len(FAILED)}")
for f in FAILED:
    print("  FAILED:", f)
sys.exit(1 if FAILED else 0)
