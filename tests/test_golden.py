"""Player-visible transcripts captured before the lifecycle change."""
from pathlib import Path
import subprocess
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / 'golden'


def transcript(command, input=None):
    result = subprocess.run([sys.executable, "-m", "harbor.cli", *command],
                            input=input, text=True, capture_output=True, cwd=ROOT,
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_demo_golden():
    assert transcript(['play', '--db', ':memory:'],
                      (ROOT / 'docs/demo-commands.txt').read_text()) == (GOLDEN / 'demo.txt').read_text()


def test_bot_golden():
    assert transcript(['bot']) == (GOLDEN / 'bot.txt').read_text()
