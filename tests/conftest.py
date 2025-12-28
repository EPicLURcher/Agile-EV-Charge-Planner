import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CC = os.path.join(ROOT, "custom_components")
if CC not in sys.path:
    sys.path.insert(0, CC)
