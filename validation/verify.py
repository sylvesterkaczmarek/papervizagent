import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

meta = json.loads((Path(__file__).parent / "case.json").read_text())
work = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
python = sys.executable
summary = {}


def run(label, command, expected=0, cwd=work):
    env = dict(os.environ, PYTHONPATH=str(cwd), PYTHONDONTWRITEBYTECODE="1",
               COVERAGE_FILE=str(evidence / (label + ".coverage")))
    with (evidence / (label + ".log")).open("w", encoding="utf-8") as output:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=output,
                                stderr=subprocess.STDOUT, timeout=180)
    text = (evidence / (label + ".log")).read_text(errors="replace")
    print(label, result.returncode, text[-200:], flush=True)
    assert result.returncode == expected, (label, text[-6000:])


def test(label, expected=0, cwd=work, coverage=False):
    args = [python, "-m", "pytest", str(cwd / meta["test"]), "-q", "--tb=short",
            "--junitxml=" + str(evidence / (label + ".xml"))]
    if coverage:
        module = "utils.config" if meta["name"] == "papervizagent" else "source.utils.geometry.geometric_utils_numpy"
        args += ["--cov=" + module, "--cov-branch",
                 "--cov-report=json:" + str(evidence / "coverage.json")]
    run(label, args, expected, cwd)
    tree = ET.parse(evidence / (label + ".xml"))
    suites = list(tree.getroot().iter("testsuite"))
    counts = [sum(int(s.attrib.get(k, 0)) for s in suites)
              for k in ("tests", "failures", "errors", "skipped")]
    failures = [t.attrib.get("classname", "") + "::" + t.attrib["name"]
                for t in tree.getroot().iter("testcase")
                if t.find("failure") is not None or t.find("error") is not None]
    summary[label] = {"counts": counts, "failed_nodes": failures}
    print(label, counts, flush=True)
    return counts


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip() == meta["head"]
run("dependencies", [python, "-m", "pip", "freeze"])
run("dependency-check", [python, "-m", "pip", "check"])
expected = [meta["new_test_count"], 0, 0, 0]
assert test("fixed", coverage=True) == expected
source = work / meta["source"]
fixed = source.read_bytes()
original = subprocess.check_output(["git", "show", meta["base"] + ":" + meta["source"]], cwd=work)
try:
    source.write_bytes(original)
    counts = test("original", expected=1)
    failure_count = meta["original_failures"]
    if os.name == "nt" and meta["name"] == "papervizagent":
        failure_count = meta["new_test_count"]
    assert counts == [meta["new_test_count"], failure_count, 0, 0], counts
finally:
    source.write_bytes(fixed)
assert test("restored") == expected
run("source-lint", [python, "-m", "ruff", "check", "--select", "E9,F63,F7,F82", meta["source"]])
run("test-lint", [python, "-m", "ruff", "check", meta["test"]])
run("test-format", [python, "-m", "ruff", "format", "--check", meta["test"]])
run("compile", [python, "-m", "py_compile", meta["source"], meta["test"]])
run("source-restoration", ["git", "diff", "--exit-code"])
run("patch-check", ["git", "diff", "--check", meta["base"]])
with tempfile.TemporaryDirectory() as folder:
    exported = Path(folder).resolve()
    prefix = "utils/" if meta["name"] == "papervizagent" else "source/"
    files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", meta["head"]], cwd=work, text=True).splitlines()
    for file in files:
        if file == meta["test"] or (file.startswith(prefix) and file.endswith(".py")):
            target = exported / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(["git", "show", meta["head"] + ":" + file], cwd=work))
    module = "utils.config" if meta["name"] == "papervizagent" else "source.utils.geometry.geometric_utils_numpy"
    code = f"import {module} as m; from pathlib import Path; p=Path(m.__file__).resolve(); print(p); assert p.is_relative_to(Path({str(exported)!r}))"
    run("exported-import", [python, "-c", code], cwd=exported)
    assert test("exported", cwd=exported) == expected
(evidence / "summary.json").write_text(json.dumps(summary, indent=2))
print("VALIDATED", meta["head"], flush=True)
