import subprocess
import sys
import time
from pathlib import Path


def test_slow_execution_with_child_process(request):
    # Deliberately leave a partial artifact while executing. Normal pytest
    # completion replaces it; timeout/cancel must not advertise it as a report.
    report_path = request.config.getoption("xmlpath")
    if report_path:
        Path(report_path).write_text("<testsuites>")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path("descendant.pid").write_text(str(child.pid))
    try:
        time.sleep(8)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=3)
