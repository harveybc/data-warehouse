"""M5 (predictor order, 2026-09-17): the repository installs correctly on its own — a wheel built
from a clean export of a published commit is byte-identical to the source, installs isolated, and
its real entry point answers. build/ and __pycache__ are no longer tracked: the 2026-09-17
adoption crash-looped the production warehouse because a tracked build/lib was packaged."""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def git(*args, **kw):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, **kw)


def test_no_generated_output_is_tracked():
    tracked = git("ls-files").stdout.splitlines()
    assert not [p for p in tracked if p.startswith("build/") or "__pycache__" in p or p.endswith(".pyc")]


@pytest.mark.packaging
def test_a_wheel_from_a_clean_export_matches_the_source_installs_isolated_and_answers(tmp_path):
    head = git("rev-parse", "HEAD").stdout.strip()
    raw = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", head],
                         capture_output=True).stdout
    export = tmp_path / "export"
    export.mkdir()
    (tmp_path / "src.tar").write_bytes(raw)
    with tarfile.open(tmp_path / "src.tar") as tar:
        tar.extractall(export)
    assert not (export / "build").exists()
    wheels = tmp_path / "wheels"
    built = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-cache-dir",
                            "-q", "-w", str(wheels), str(export)], capture_output=True, text=True)
    assert built.returncode == 0, built.stderr[-800:]
    wheel = next(wheels.glob("*.whl"))
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if name.endswith(".py") and ".dist-info/" not in name:
                assert (export / name).read_bytes() == z.read(name), name
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True)
    py = venv / "bin" / "python"
    inst = subprocess.run([str(py), "-m", "pip", "install", "--no-deps", "--no-cache-dir", "-q",
                           str(wheel)], capture_output=True, text=True)
    assert inst.returncode == 0, inst.stderr[-800:]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    answer = subprocess.run([str(py), "-c",
                             "import data_warehouse_service, data_warehouse_service.main as m; "
                             "print(data_warehouse_service.__file__)"],
                            capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert answer.returncode == 0, answer.stderr[-600:]
    assert str(venv) in answer.stdout
    helped = subprocess.run([str(py), "-m", "data_warehouse_service.main", "--help"],
                            capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert helped.returncode == 0 and "load_config" in helped.stdout + helped.stderr
