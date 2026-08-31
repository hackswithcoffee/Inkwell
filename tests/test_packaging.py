"""The two entry points must keep working: the CLI and the extractor subprocess."""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cli_entry_point_imports():
    """scribe_pipeline.py is what the launchd watcher runs."""
    out = subprocess.run(
        [sys.executable, "-c", "import scribe_pipeline; print(scribe_pipeline.run_pipeline.__name__)"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "run_pipeline" in out.stdout


def test_extractor_runs_as_a_module():
    """extract.py spawns the extractor with -m; the module must be executable."""
    out = subprocess.run(
        [sys.executable, "-m", "inkwell.extractor", "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "transcript_path" in out.stdout
