"""pytest 插件：只收集时导出目录，实际执行时把用例 ID 写入 JUnit。

--collect-only 会导入测试模块，但不会调用用例或执行 fixture 的数据准备。
"""
import json
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption("--probe-collection-output", help="Write collected cases as JSON")


def pytest_collection_modifyitems(items):
    for item in items:
        item.user_properties.append(("probe.case_id", item.nodeid))


def pytest_collection_finish(session):
    output = session.config.getoption("--probe-collection-output")
    if not output:
        return
    cases = []
    for item in session.items:
        file, line, _ = item.location
        cases.append({
            "id": item.nodeid,
            "name": item.name,
            "function": getattr(item, "originalname", None) or item.name,
            "source_file": file,
            "source_line": line + 1,
            "parameters": {name: repr(value) for name, value in getattr(getattr(item, "callspec", None), "params", {}).items()},
            "requires_postgres": item.get_closest_marker("postgres") is not None or "database" in item.fixturenames,
        })
    Path(output).write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
