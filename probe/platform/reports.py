from pathlib import Path
import json
from xml.etree import ElementTree


def parse_junit(path: Path) -> dict:
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("JUnit report is larger than 10 MiB")
    root = ElementTree.parse(path).getroot()
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Not a JUnit report")
    cases = []
    counts = {"total": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for case in root.iter("testcase"):
        outcome, details = "passed", ""
        for tag, candidate in [("error", "error"), ("failure", "failed"), ("skipped", "skipped")]:
            node = case.find(tag)
            if node is not None:
                outcome = candidate
                details = ((node.get("message") or "") + "\n" + (node.text or "")).strip()[:20_000]
                break
        counts["total"] += 1
        counts[outcome] += 1
        evidence = []
        case_id = None
        for prop in case.findall("./properties/property"):
            value = prop.get("value", "")
            if prop.get("name") == "probe.case_id":
                case_id = value
            if prop.get("name") != "probe.evidence" or len(value) > 65_536:
                continue
            try:
                document = json.loads(value)
                required = {"request", "response", "database"}
                if isinstance(document, dict) and document.get("version") == 1 and isinstance(document.get("label"), str) and all(isinstance(document.get(name), dict) for name in required) and isinstance(document.get("checks"), list):
                    if all(isinstance(check, dict) and isinstance(check.get("name"), str) and {"expected", "actual"} <= check.keys() for check in document["checks"]):
                        if not all(isinstance(document["request"].get(key), str) for key in ["method", "url", "path"]):
                            continue
                        if not isinstance(document["request"].get("headers"), dict) or not isinstance(document["response"].get("headers"), dict):
                            continue
                        if "status_code" not in document["response"]:
                            continue
                        status_code = document["response"]["status_code"]
                        if status_code is None:
                            error = document.get("transport_error")
                            if not isinstance(error, dict) or not all(isinstance(error.get(key), str) for key in ["type", "phase", "message"]):
                                continue
                        elif not isinstance(status_code, int):
                            continue
                        evidence.append(document)
            except (ValueError, TypeError):
                pass  # 不完整的可选证据不改变真实用例的通过／失败结果。
        line = case.get("line")
        cases.append({
            "name": case.get("name", "unnamed"),
            "case_id": case_id,
            "classname": case.get("classname", ""),
            "outcome": outcome,
            "duration_seconds": float(case.get("time", "0")),
            "details": details,
            "source_file": case.get("file"),
            "source_line": int(line) + 1 if line is not None and line.isdigit() else None,
            "evidence": evidence,
        })
    return {"counts": counts, "cases": cases}


def classify_result(exit_code: int, report: dict) -> str:
    counts = report["counts"]
    if exit_code == 0 and counts["passed"] > 0 and counts["failed"] == counts["error"] == 0:
        return "passed"
    if exit_code == 1 and counts["failed"] + counts["error"] > 0:
        return "failed"
    return "error"
