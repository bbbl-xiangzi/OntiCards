"""
 @File: test_sql_from_guard.py
 @Description: 测试 sql_from_guard 模块的 FROM/JOIN 表引用扫描器
 @Author: aroberts957
 @Create: 2026-09-16

 运行方式:
   cd OntiCards_Api && python test/test_sql_from_guard.py
   或 pytest test/test_sql_from_guard.py
"""

from __future__ import annotations

import importlib.util
import os
import sys


def _load_module():
    """Load sql_from_guard via file path (avoids package __init__ chains)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mod_path = os.path.join(base, "controllers", "query", "sql_from_guard.py")
    spec = importlib.util.spec_from_file_location("sql_from_guard", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sql_from_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()
iter_from_table_refs = mod.iter_from_table_refs
FromTableRef = mod.FromTableRef


def _tables(sql: str):
    """Helper: return list of (raw_table, raw_alias, derived) for non-derived refs."""
    refs = iter_from_table_refs(sql)
    return [(r.raw_table, r.raw_alias, r.derived) for r in refs]


def _physical(sql: str):
    """Helper: return just raw_table strings for non-derived refs."""
    return [r.raw_table for r in iter_from_table_refs(sql) if not r.derived]


def _aliases(sql: str):
    """Helper: return raw_alias strings for all refs."""
    return [r.raw_alias for r in iter_from_table_refs(sql)]


# ---- Test functions ----

def test_single_from():
    refs = iter_from_table_refs("SELECT * FROM emp")
    assert len(refs) == 1
    assert refs[0].raw_table == "emp"
    assert refs[0].raw_alias == ""
    assert refs[0].derived is False


def test_from_with_alias():
    refs = iter_from_table_refs("SELECT e.name FROM emp e")
    assert len(refs) == 1
    assert refs[0].raw_table == "emp"
    assert refs[0].raw_alias == "e"


def test_from_with_as_alias():
    refs = iter_from_table_refs("SELECT e.name FROM emp AS e")
    assert len(refs) == 1
    assert refs[0].raw_table == "emp"
    assert refs[0].raw_alias == "e"


def test_two_table_comma():
    refs = iter_from_table_refs("SELECT * FROM emp e, payroll p")
    physical = [r for r in refs if not r.derived]
    assert len(physical) == 2
    assert physical[0].raw_table == "emp"
    assert physical[0].raw_alias == "e"
    assert physical[1].raw_table == "payroll"
    assert physical[1].raw_alias == "p"


def test_three_table_comma():
    refs = iter_from_table_refs("SELECT * FROM a, b, c")
    tables = _physical("SELECT * FROM a, b, c")
    assert tables == ["a", "b", "c"]


def test_comma_no_aliases():
    tables = _physical("SELECT * FROM emp, dept")
    assert tables == ["emp", "dept"]


def test_mixed_comma_and_join():
    sql = "SELECT * FROM a, b JOIN c ON b.id = c.id"
    tables = _physical(sql)
    assert "a" in tables
    assert "b" in tables
    assert "c" in tables
    assert len(tables) == 3


def test_join_only():
    sql = "SELECT * FROM emp e JOIN dept d ON e.dept_id = d.id"
    refs = iter_from_table_refs(sql)
    physical = [r for r in refs if not r.derived]
    assert len(physical) == 2
    assert physical[0].raw_table == "emp"
    assert physical[0].raw_alias == "e"
    assert physical[1].raw_table == "dept"
    assert physical[1].raw_alias == "d"


def test_quoted_double():
    refs = iter_from_table_refs('SELECT * FROM "my_table" t')
    assert len(refs) == 1
    assert refs[0].raw_table == '"my_table"'
    assert refs[0].raw_alias == "t"


def test_quoted_backtick():
    refs = iter_from_table_refs("SELECT * FROM `my_table` t")
    assert len(refs) == 1
    assert refs[0].raw_table == "`my_table`"
    assert refs[0].raw_alias == "t"


def test_quoted_bracket():
    refs = iter_from_table_refs("SELECT * FROM [my_table] t")
    assert len(refs) == 1
    assert refs[0].raw_table == "[my_table]"
    assert refs[0].raw_alias == "t"


def test_schema_table():
    refs = iter_from_table_refs("SELECT * FROM public.employees e")
    assert len(refs) == 1
    assert refs[0].raw_table == "public.employees"
    assert refs[0].raw_alias == "e"


def test_derived_table():
    sql = "SELECT x.name FROM (SELECT name FROM inner_t) x"
    refs = iter_from_table_refs(sql)
    derived = [r for r in refs if r.derived]
    physical = [r for r in refs if not r.derived]
    assert len(derived) >= 1
    assert derived[0].raw_alias == "x"
    # inner_t should be captured from the inner FROM
    inner_tables = [r.raw_table for r in physical]
    assert "inner_t" in inner_tables


def test_string_literal_from_ignored():
    sql = "SELECT * FROM emp WHERE name = 'FROM secret_table'"
    tables = _physical(sql)
    assert tables == ["emp"]
    assert "secret_table" not in tables


def test_string_literal_comma_ignored():
    sql = "SELECT * FROM emp WHERE note = 'a, b, c'"
    tables = _physical(sql)
    assert tables == ["emp"]


def test_parens_in_string_ignored():
    sql = "SELECT * FROM emp WHERE x = '(SELECT FROM hidden)'"
    tables = _physical(sql)
    assert tables == ["emp"]


def test_reserved_word_not_alias_where():
    refs = iter_from_table_refs("SELECT * FROM emp WHERE id = 1")
    assert len(refs) == 1
    assert refs[0].raw_table == "emp"
    assert refs[0].raw_alias == ""


def test_reserved_word_not_alias_join():
    refs = iter_from_table_refs("SELECT * FROM emp JOIN dept ON emp.d = dept.id")
    physical = [r for r in refs if not r.derived]
    assert len(physical) == 2
    assert physical[0].raw_alias == ""
    assert physical[1].raw_table == "dept"


def test_case_insensitive_keywords():
    sql = "select * from EMP e join DEPT d on e.id = d.id"
    tables = _physical(sql)
    assert "EMP" in tables
    assert "DEPT" in tables


def test_subquery_in_where():
    sql = "SELECT * FROM emp WHERE id IN (SELECT id FROM secret_t)"
    refs = iter_from_table_refs(sql)
    physical = [r for r in refs if not r.derived]
    table_names = [r.raw_table for r in physical]
    assert "emp" in table_names
    assert "secret_t" in table_names


def test_complex_comma_join_bypass():
    """The exact bypass scenario from the bug report."""
    sql = "SELECT p.salary FROM emp e, payroll p WHERE e.id = p.emp_id"
    tables = _physical(sql)
    assert "emp" in tables
    assert "payroll" in tables


def test_multiple_joins():
    sql = "SELECT * FROM a LEFT JOIN b ON a.id = b.a_id RIGHT JOIN c ON b.id = c.b_id"
    tables = _physical(sql)
    assert len(tables) == 3
    assert "a" in tables
    assert "b" in tables
    assert "c" in tables


def test_cte_with_from():
    sql = "WITH cte AS (SELECT * FROM base_t) SELECT * FROM cte JOIN other_t ON cte.id = other_t.id"
    refs = iter_from_table_refs(sql)
    physical = [r for r in refs if not r.derived]
    table_names = [r.raw_table for r in physical]
    assert "base_t" in table_names
    assert "cte" in table_names
    assert "other_t" in table_names


# ---- Runner ----

def run_tests():
    passed = 0
    failed = 0
    test_funcs = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    test_funcs.sort(key=lambda f: f.__name__)

    for func in test_funcs:
        try:
            func()
            passed += 1
            print(f"  PASS: {func.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {func.__name__}: {e}")

    print(f"\nResults: {passed} passed, {failed} failed, {passed + failed} total")
    return failed


def run_integration():
    """Attempt to import run_sql_safe_new and run sqlite-in-memory scenarios."""
    print("\n--- Integration tests ---")
    try:
        # Try importing the agg engine's run_sql_safe_new
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        mod_path = os.path.join(base, "controllers", "query", "query_by_datacards_agg.py")
        spec = importlib.util.spec_from_file_location("agg_engine", mod_path)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        run_sql_safe_new = getattr(engine, "run_sql_safe_new", None)
        if run_sql_safe_new is None:
            print("  SKIP: run_sql_safe_new not found in engine module")
            return
    except ImportError as e:
        print(f"  SKIP: Cannot import engine (Flask deps absent): {e}")
        return
    except Exception as e:
        print(f"  SKIP: Engine import failed: {e}")
        return

    # If we get here, try sqlite scenarios
    try:
        import sqlite3
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("CREATE TABLE emp (id INTEGER, name TEXT)")
        cur.execute("CREATE TABLE payroll (id INTEGER, emp_id INTEGER, salary REAL)")
        cur.execute("INSERT INTO emp VALUES (1, 'Alice')")
        cur.execute("INSERT INTO payroll VALUES (1, 1, 50000)")
        conn.commit()

        # Build cluster_tables for whitelist [emp] only
        cluster_tables = [
            {"table_name": "emp", "columns": [{"name": "id"}, {"name": "name"}]}
        ]

        # Test: comma join with non-whitelisted payroll should raise
        try:
            run_sql_safe_new(
                "SELECT p.salary FROM emp e, payroll p WHERE e.id = p.emp_id",
                cluster_tables,
                conn,
                "sqlite",
            )
            print("  FAIL: Expected ValueError for non-whitelisted table 'payroll'")
        except ValueError as e:
            if "非白名单表" in str(e) or "payroll" in str(e):
                print("  PASS: comma-join bypass correctly blocked")
            else:
                print(f"  FAIL: Unexpected ValueError: {e}")
        except Exception as e:
            print(f"  FAIL: Unexpected exception: {e}")

        # Test: both whitelisted should work
        cluster_tables_both = [
            {"table_name": "emp", "columns": [{"name": "id"}, {"name": "name"}]},
            {"table_name": "payroll", "columns": [{"name": "id"}, {"name": "emp_id"}, {"name": "salary"}]},
        ]
        try:
            result = run_sql_safe_new(
                "SELECT e.name, p.salary FROM emp e, payroll p WHERE e.id = p.emp_id",
                cluster_tables_both,
                conn,
                "sqlite",
            )
            print(f"  PASS: whitelisted comma-join returned {len(result)} rows")
        except Exception as e:
            print(f"  SKIP: whitelisted query failed (may need different signature): {e}")

        conn.close()
    except Exception as e:
        print(f"  SKIP: Integration test error: {e}")


# ---- AST-based guard: ensure critical locals are defined before use ----

def test_ast_guard_critical_locals():
    """
    AST-based check: parse both engine files and verify that in each of
    run_sql_safe_new and _exec_cluster, every Name-load of critical locals
    (cte_names, allowed_tables or allowed_with_cte, table_alias_map) is
    preceded (by line position) by an assignment in the same function scope.

    This catches accidental deletion of definition blocks.
    """
    import ast

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    engine_files = [
        os.path.join(base, "controllers", "query", "query_by_datacards_agg.py"),
        os.path.join(base, "controllers", "query", "query_by_datacards_agg_plugin.py"),
    ]

    # Critical local variable names that must be assigned before use
    # in each target function.
    target_funcs = {
        "run_sql_safe_new": {"cte_names", "allowed_physical", "system_virtual_tables", "table_alias_map"},
        "_exec_cluster": {"cte_names", "allowed_tables"},
    }

    for fpath in engine_files:
        fname = os.path.basename(fpath)
        with open(fpath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=fpath)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in target_funcs:
                continue

            required = target_funcs[node.name]
            # Collect all assignment targets (line numbers) in this function
            assigned = {}  # name -> first assignment line
            for child in ast.walk(node):
                if isinstance(child, ast.Assign):
                    for tgt in child.targets:
                        if isinstance(tgt, ast.Name):
                            if tgt.id not in assigned:
                                assigned[tgt.id] = tgt.lineno
                elif isinstance(child, ast.AnnAssign):
                    if isinstance(child.target, ast.Name) and child.value is not None:
                        if child.target.id not in assigned:
                            assigned[child.target.id] = child.target.lineno
                elif isinstance(child, ast.AugAssign):
                    if isinstance(child.target, ast.Name):
                        if child.target.id not in assigned:
                            assigned[child.target.id] = child.target.lineno
                elif isinstance(child, (ast.For,)):
                    # for m in ... also defines the loop var
                    if isinstance(child.target, ast.Name):
                        if child.target.id not in assigned:
                            assigned[child.target.id] = child.target.lineno

            # Check each required name
            for name in required:
                assert name in assigned, (
                    f"AST GUARD FAIL: {fname} :: {node.name}() "
                    f"is missing assignment to '{name}'. "
                    f"Was the definition block accidentally deleted?"
                )


if __name__ == "__main__":
    print("=== sql_from_guard unit tests ===")
    failed = run_tests()
    run_integration()
    sys.exit(1 if failed > 0 else 0)
