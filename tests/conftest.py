"""Put the pack root on sys.path so tests import the modules the way ComfyUI does."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
