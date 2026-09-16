"""界面上增删改项目，写回 projects.yaml。

用 ruamel.yaml 的 round-trip 模式：改哪个键只动哪个键，用户手写的注释和顺序原样保留。
每次操作都是「读文件 → 改 → 原子写回」，不缓存文档对象，和手改文件交错也不会互相覆盖。
"""

from __future__ import annotations

import io
import os
import re
import threading
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .config import KNOWN_KEYS

_lock = threading.Lock()


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)   # 和手写的 `  - id:` 缩进一致
    y.width = 4096                               # 长命令不折行
    return y


class EditError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def clean(raw: dict) -> dict:
    """表单送来的原始映射 → 要写进 yaml 的映射。空值不写，默认值不写，文件保持干净。"""
    out: dict = {}
    for k in ("id", "name", "cwd", "cmd", "group", "url", "url_pattern", "env_file"):
        v = raw.get(k)
        if v is None:
            continue
        v = str(v).strip()
        if v:
            out[k] = v
    if out.get("name") == out.get("id"):
        out.pop("name", None)   # name 缺省就是 id，不用重复写
    if "cwd" in out:
        out["cwd"] = out["cwd"].replace("\\", "/")   # 手写的文件都是正斜杠，保持一致
    port = raw.get("port")
    if port not in (None, ""):
        try:
            out["port"] = int(port)
        except (TypeError, ValueError):
            out["port"] = port   # 留给校验去报错
    if raw.get("autostart"):
        out["autostart"] = True
    restart = str(raw.get("restart") or "never")
    if restart != "never":
        out["restart"] = restart
    env = raw.get("env") or {}
    if isinstance(env, dict):
        env = {str(k).strip(): str(v) for k, v in env.items() if str(k).strip()}
        if env:
            out["env"] = env
    return out


def _load(path: Path) -> tuple[YAML, CommentedMap, CommentedSeq]:
    y = _yaml()
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        data = y.load(text)
    else:
        text, data = "", None
    y.blank_between_items = _uses_blank(text)   # type: ignore[attr-defined]  # 原文件的风格，写回时沿用
    if data is None:
        data = CommentedMap()
    if not isinstance(data, CommentedMap):
        raise EditError(500, "配置文件顶层不是映射，不敢改；请手动修一下")
    seq = data.get("projects")
    if seq is None:
        seq = CommentedSeq()
        data["projects"] = seq
    if not isinstance(seq, CommentedSeq):
        raise EditError(500, "projects 不是列表，不敢改；请手动修一下")
    return y, data, seq


def _dump(y: YAML, data: CommentedMap, path: Path) -> None:
    buf = io.StringIO()
    y.dump(data, buf)
    text = _tidy_blank_lines(buf.getvalue(), getattr(y, "blank_between_items", True))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")   # 别让 Windows 写成 CRLF
    os.replace(tmp, path)


_ITEM = re.compile(r"^  - ")


def _uses_blank(text: str) -> bool:
    """文件是不是用空行分隔项目。空文件 / 只有一项的按「是」。"""
    lines = text.split("\n")
    items = [i for i, ln in enumerate(lines) if _ITEM.match(ln)]
    return len(items) < 2 or any(i > 0 and lines[i - 1] == "" for i in items)


def _tidy_blank_lines(text: str, uses_blank: bool) -> str:
    """项目之间的空行在 ruamel 里挂在前一项上，删除 / 重排后会跟着跑。
    这里按文本整理：如果文件本来就用空行分隔项目，那就保证每项前面恰好一个空行；末尾不留空行。"""
    lines = text.split("\n")
    out: list[str] = []
    for ln in lines:
        if _ITEM.match(ln) and out:
            prev = out[-1]
            if uses_blank and prev != "" and not prev.rstrip().endswith(":") and not prev.lstrip().startswith("#"):
                out.append("")
        if ln == "" and out and out[-1] == "":
            continue   # 连续空行压成一个
        out.append(ln)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def _find(seq: CommentedSeq, project_id: str) -> int:
    for i, item in enumerate(seq):
        if isinstance(item, dict) and str(item.get("id", "")).strip() == project_id:
            return i
    raise EditError(404, f"没有这个项目：{project_id}")


def _apply(m: CommentedMap, raw: dict) -> None:
    """把 raw 里的键写进已有映射：有的原位改，没有的追加，raw 里没给的已知键删掉。不认识的键不碰。"""
    for k in KNOWN_KEYS:
        if k not in raw:
            if k in m:
                del m[k]
            continue
        v = raw[k]
        if k == "env":
            cur = m.get("env")
            if not isinstance(cur, CommentedMap):
                cur = CommentedMap()
                m["env"] = cur
            for ek in list(cur.keys()):
                if ek not in v:
                    del cur[ek]
            for ek, ev in v.items():
                cur[ek] = ev
        elif k in m:
            m[k] = v
        else:
            _insert_in_order(m, k, v)


def _insert_in_order(m: CommentedMap, key: str, value) -> None:
    """新键插到它在 KNOWN_KEYS 里的相对位置（name 跟在 id 后面），不是一律追加到末尾。"""
    rank = {k: i for i, k in enumerate(KNOWN_KEYS)}
    pos = 0
    for i, existing in enumerate(m.keys()):
        if rank.get(existing, -1) < rank[key]:
            pos = i + 1
    m.insert(pos, key, value)


def _new_map(raw: dict) -> CommentedMap:
    m = CommentedMap()
    for k in KNOWN_KEYS:
        if k in raw:
            m[k] = CommentedMap(raw[k]) if k == "env" else raw[k]
    return m


def add_project(path: Path, raw: dict) -> None:
    raw = clean(raw)
    with _lock:
        y, data, seq = _load(path)
        pid = raw.get("id", "")
        if any(isinstance(it, dict) and it.get("id") == pid for it in seq):
            raise EditError(409, f"id 重复：{pid}")
        seq.append(_new_map(raw))
        _dump(y, data, path)   # 项目之间的空行由 _tidy_blank_lines 补


def update_project(path: Path, project_id: str, raw: dict) -> None:
    raw = clean(raw)
    raw["id"] = project_id    # id 不可改
    with _lock:
        y, data, seq = _load(path)
        _apply(seq[_find(seq, project_id)], raw)
        _dump(y, data, path)


def delete_project(path: Path, project_id: str) -> None:
    with _lock:
        y, data, seq = _load(path)
        i = _find(seq, project_id)
        del seq[i]
        # 跟在被删项后面的注释会挂在它的索引上，一并去掉
        seq.ca.items.pop(i, None)
        _dump(y, data, path)


def reorder(path: Path, order: list[dict]) -> None:
    """按 order 里的顺序重排，顺便改 group（拖到别的组）。没提到的项目保持原相对顺序排在最后。"""
    with _lock:
        y, data, seq = _load(path)
        by_id: dict[str, int] = {}
        for i, item in enumerate(seq):
            if isinstance(item, dict) and item.get("id"):
                by_id[str(item["id"])] = i
        new_idx: list[int] = []
        for ent in order:
            i = by_id.get(str(ent.get("id", "")))
            if i is None or i in new_idx:
                continue
            new_idx.append(i)
            item = seq[i]
            group = ent.get("group")
            group = str(group).strip() if group is not None else ""
            if group and "group" in item:
                item["group"] = group
            elif group:
                _insert_in_order(item, "group", group)
            elif "group" in item:
                del item["group"]
        new_idx += [i for i in range(len(seq)) if i not in new_idx]
        if new_idx == list(range(len(seq))):
            _dump(y, data, path)   # 只改了 group
            return
        items = [seq[i] for i in new_idx]
        comments = {k: seq.ca.items.get(i) for k, i in enumerate(new_idx) if i in seq.ca.items}
        del seq[:]
        seq.ca.items.clear()
        seq.extend(items)
        seq.ca.items.update(comments)
        _dump(y, data, path)
