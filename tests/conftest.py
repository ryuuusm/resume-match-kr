"""Make the skill scripts importable from tests without installing anything."""
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "resume-match-kr"
sys.path.insert(0, str(SKILL / "scripts"))
