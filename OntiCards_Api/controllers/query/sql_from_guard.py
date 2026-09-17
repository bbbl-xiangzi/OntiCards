"""
 @File: sql_from_guard.py
 @Description: 括号/引号感知的 FROM/JOIN 表引用扫描器
 @Author: aroberts957
 @Create: 2026-09-16

 替代一次性正则 (?:FROM|JOIN)\\s+<identifier>，该正则只能捕获每个 FROM/JOIN
 后的第一个表名，逗号隐式连接 (FROM a, b) 中第二个及之后的表会被遗漏。

 本模块逐字符遍历 SQL，跟踪括号深度和引号状态（单引号、双引号、反引号、方括号），
 在每个 FROM/JOIN 关键字后遍历逗号分隔的引用列表。

 注意事项：
 - 调用方应保持已有的 EXTRACT/SUBSTRING/POSITION/TRIM 函数预替换（扫描器统一
   处理每个 FROM/JOIN 出现，不做函数上下文区分）。
 - 形如 FROM (a JOIN b) 的括号连接表达式会产生一个 derived ref，同时内部的
   JOIN 关键字也会被独立扫描——这是可接受的（内部表会被额外捕获）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

# 表名/别名后如果出现这些关键字，说明不是别名而是下一个子句的开始。
RESERVED_AFTER_TABLE = frozenset({
    "JOIN", "LEFT", "RIGHT", "FULL", "INNER", "OUTER", "CROSS", "STRAIGHT_JOIN",
    "ON", "WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "FETCH",
    "UNION", "EXCEPT", "INTERSECT", "WINDOW", "VALUES", "SET", "FOR",
    "AND", "OR", "USING",
})


@dataclass
class FromTableRef:
    """FROM/JOIN 子句中提取的单个表引用。"""
    raw_table: str       # 原始表名文本（含引号/schema前缀等）
    raw_alias: str       # 原始别名文本（无别名时为空字符串）
    derived: bool        # True 表示是派生表（子查询），如 FROM (...) x


# ---------- 内部扫描工具 ----------

_WS = frozenset(" \t\n\r")


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_" or ch == "$" or ord(ch) > 127


def _is_ident_cont(ch: str) -> bool:
    return ch.isalnum() or ch in ("_", "$", "-") or ord(ch) > 127


def _skip_ws(sql: str, i: int) -> int:
    n = len(sql)
    while i < n and sql[i] in _WS:
        i += 1
    return i


def _read_quoted(sql: str, i: int) -> tuple:
    """Read a quoted identifier. Returns (text_with_quotes, end_pos)."""
    n = len(sql)
    if i >= n:
        return ("", i)
    ch = sql[i]
    if ch == "`":
        end_ch = "`"
    elif ch == '"':
        end_ch = '"'
    elif ch == "[":
        end_ch = "]"
    else:
        return ("", i)
    j = i + 1
    while j < n:
        if sql[j] == end_ch:
            j += 1
            return (sql[i:j], j)
        j += 1
    return (sql[i:j], j)


def _read_ident(sql: str, i: int) -> tuple:
    """Read an unquoted identifier (may include schema.table dots). Returns (text, end_pos)."""
    n = len(sql)
    if i >= n or not _is_ident_start(sql[i]):
        return ("", i)
    j = i
    while j < n and _is_ident_cont(sql[j]):
        j += 1
    # Support schema.table paths with dots
    while j < n and sql[j] == ".":
        j += 1
        if j < n and (sql[j] in ("`", '"', "[") or _is_ident_start(sql[j])):
            if sql[j] in ("`", '"', "["):
                seg, j = _read_quoted(sql, j)
                if not seg:
                    break
            else:
                k = j
                while k < n and _is_ident_cont(sql[k]):
                    k += 1
                j = k
        else:
            break
    return (sql[i:j], j)


def _read_ref_identifier(sql: str, i: int) -> tuple:
    """Read one identifier (quoted or unquoted), used for table names or aliases."""
    n = len(sql)
    if i >= n:
        return ("", i)
    if sql[i] in ("`", '"', "["):
        return _read_quoted(sql, i)
    return _read_ident(sql, i)


def _consume_balanced_parens(sql: str, i: int) -> int:
    """From a ( consume balanced parens (quote-aware), return position after )."""
    n = len(sql)
    if i >= n or sql[i] != "(":
        return i
    depth = 1
    i += 1
    in_str = None
    in_ident_quote = None
    while i < n and depth > 0:
        ch = sql[i]
        if in_str:
            if ch == "'" and i + 1 < n and sql[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_str = None
        elif in_ident_quote:
            if ch == in_ident_quote:
                in_ident_quote = None
        else:
            if ch == "'":
                in_str = "'"
            elif ch == "`":
                in_ident_quote = "`"
            elif ch == '"':
                in_ident_quote = '"'
            elif ch == "[":
                in_ident_quote = "]"
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        i += 1
    return i


# ---------- Main scanner ----------

_KW_RE = re.compile(r"\b(?:FROM|JOIN)\b", re.IGNORECASE)


def iter_from_table_refs(sql: str) -> List[FromTableRef]:
    """
    Scan SQL text and extract all FROM/JOIN table references (including comma-
    separated implicit joins).

    Returns a list of FromTableRef. Derived tables (subqueries) have
    derived=True and raw_table is an empty string.
    """
    result: List[FromTableRef] = []
    n = len(sql)

    # Step 1: Mark single-quoted string literal regions to avoid matching keywords inside them
    in_literal = [False] * (n + 1)
    i = 0
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            in_literal[i] = True
            while j < n:
                in_literal[j] = True
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    in_literal[j + 1] = True
                    j += 2
                    continue
                if sql[j] == "'":
                    j += 1
                    break
                j += 1
            i = j
        else:
            i += 1

    # Step 2: Find all FROM/JOIN keywords not inside string literals
    for m in _KW_RE.finditer(sql):
        kw_start = m.start()
        if in_literal[kw_start]:
            continue
        pos = _skip_ws(sql, m.end())

        while pos < n:
            pos = _skip_ws(sql, pos)
            if pos >= n:
                break

            ch = sql[pos]

            # Derived table: (SELECT ...)
            if ch == "(":
                end = _consume_balanced_parens(sql, pos)
                alias_pos = _skip_ws(sql, end)
                alias_text = ""
                if alias_pos < n:
                    if (alias_pos + 2 <= n
                            and sql[alias_pos:alias_pos + 2].upper() == "AS"
                            and (alias_pos + 2 >= n or not _is_ident_cont(sql[alias_pos + 2]))):
                        alias_pos = _skip_ws(sql, alias_pos + 2)
                    if alias_pos < n and (sql[alias_pos] in ("`", '"', "[") or _is_ident_start(sql[alias_pos])):
                        maybe_alias, alias_end = _read_ref_identifier(sql, alias_pos)
                        bare = maybe_alias.strip("`\"[]").upper()
                        if bare not in RESERVED_AFTER_TABLE:
                            alias_text = maybe_alias
                            pos = alias_end
                        else:
                            pos = end
                    else:
                        pos = end
                else:
                    pos = end
                result.append(FromTableRef(raw_table="", raw_alias=alias_text, derived=True))
            elif ch in ("`", '"', "[") or _is_ident_start(ch):
                tbl_text, tbl_end = _read_ref_identifier(sql, pos)
                if not tbl_text:
                    break
                alias_pos = _skip_ws(sql, tbl_end)
                alias_text = ""
                if alias_pos < n:
                    if (alias_pos + 2 <= n
                            and sql[alias_pos:alias_pos + 2].upper() == "AS"
                            and (alias_pos + 2 >= n or not _is_ident_cont(sql[alias_pos + 2]))):
                        alias_pos = _skip_ws(sql, alias_pos + 2)
                    if alias_pos < n and (sql[alias_pos] in ("`", '"', "[") or _is_ident_start(sql[alias_pos])):
                        maybe_alias, alias_end = _read_ref_identifier(sql, alias_pos)
                        bare = maybe_alias.strip("`\"[]").upper()
                        if bare not in RESERVED_AFTER_TABLE:
                            alias_text = maybe_alias
                            pos = alias_end
                        else:
                            pos = tbl_end
                    else:
                        pos = tbl_end
                else:
                    pos = tbl_end
                result.append(FromTableRef(raw_table=tbl_text, raw_alias=alias_text, derived=False))
            else:
                break

            # Check for comma (more refs follow)
            pos = _skip_ws(sql, pos)
            if pos < n and sql[pos] == ",":
                pos += 1
            else:
                break

    return result
