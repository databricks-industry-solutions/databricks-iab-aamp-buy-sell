import os
import sys

# Make registry_mcp importable for tests without an editable install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
