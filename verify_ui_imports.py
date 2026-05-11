import sys
import os
try:
    from fd_terminal.ui import SandboxSelectScreen
    print("SandboxSelectScreen imported successfully.")
    from fd_terminal.main import FinalDestinationApp
    print("FinalDestinationApp imported successfully.")
except Exception as e:
    print(f"Import Error: {e}")
    sys.exit(1)
