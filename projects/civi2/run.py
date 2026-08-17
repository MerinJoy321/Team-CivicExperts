"""CivicPilot Application Launcher.

Starts the web server and automatically opens http://127.0.0.1:8000 in your browser.
Auto-detects and uses .venv python environment.
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
import uvicorn

def open_browser():
    time.sleep(1.5)
    print("\nOpening CivicPilot Web UI in your default browser at http://127.0.0.1:8000 ...\n")
    webbrowser.open("http://127.0.0.1:8000")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("civicpilot.web.server:app", 
                host="127.0.0.1", 
                port=8000)
