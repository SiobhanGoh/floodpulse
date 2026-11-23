import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                           # for the CDK stack
sys.path.insert(0, str(ROOT / "lambdas" / "collector"))  # for jps.py and handler.py

FIXTURES = Path(__file__).resolve().parent / "fixtures"
