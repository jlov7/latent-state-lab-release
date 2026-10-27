"""Run the literal supplied-fixture README workflow against installed commands."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def block(text, name):
    section = text.split(f"<!-- {name}:start -->", 1)[1].split(
        f"<!-- {name}:end -->", 1)[0].strip().splitlines()
    fence = chr(96) * 3
    if not section[0].startswith(fence) or section[-1] != fence:
        raise ValueError("README workflow must be one fenced block")
    return "\n".join(section[1:-1]) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bin", type=Path)
    parser.add_argument("--keep-work", type=Path)
    args = parser.parse_args()
    bin_dir = args.bin.resolve()
    if not (bin_dir / "latent-state-patch").is_file() or not (bin_dir / "python").is_file():
        parser.error("bin must contain installed commands and Python")
    scratch = ROOT / ".release-scratch"
    scratch.mkdir(exist_ok=True)
    if args.keep_work:
        args.keep_work.mkdir(parents=True, exist_ok=False)
    context = (nullcontext(str(args.keep_work.resolve())) if args.keep_work else
               tempfile.TemporaryDirectory(prefix="readme-", dir=scratch))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    script, expected = block(readme, "quickstart"), block(readme, "quickstart-output")
    with context as directory:
        work = Path(directory)
        (work / ".venv").symlink_to(bin_dir.parent, target_is_directory=True)
        (work / "examples").mkdir()
        shutil.copyfile(ROOT / "examples/correction_batch.py",
                        work / "examples/correction_batch.py")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(["/bin/sh", "-eu", "-c", script], cwd=work, env=env,
                                capture_output=True, text=True, timeout=60, check=False)
        report = {"argv": ["/bin/sh", "-eu", "-c", script], "returncode": result.returncode,
                  "stdout": result.stdout, "stderr": result.stderr, "expected_stdout": expected}
        (work / "COMMAND.json").write_text(json.dumps(report, indent=2) + "\n")
        if result.returncode or result.stdout != expected or result.stderr:
            raise RuntimeError("README workflow failed; inspect retained COMMAND.json")
        applied = json.loads((work / "correction-demo/applied.json").read_text())
        replayed = json.loads((work / "correction-demo/replayed.json").read_text())
        assert applied["store_written"] and applied["changes"]["applicable"] == 3
        assert not replayed["store_written"] and replayed["changes"]["already_present"] == 3
        summary = {"status": "pass", "literal_readme_commands": True,
                   "first_apply": applied["changes"], "repeat_apply": replayed["changes"],
                   "state_sha256": hashlib.sha256(
                       (work / "correction-demo/billing.state").read_bytes()).hexdigest()}
        (work / "RESULT.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
