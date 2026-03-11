#!/usr/bin/env python3
"""
Gravitee Initialisation Orchestrator for the RNLI Lifeboat Station Finder demo.

Runs:
  1. am_init.py  - Sets up Gravitee AM (domain, app, user, MCP server)
  2. init.py     - Sets up Gravitee APIM (APIs, plans)
"""

import subprocess
import sys


def log(msg: str):
    print(f"[gravitee-init] {msg}", flush=True)


def run_script(script_path: str, label: str) -> bool:
    log(f"Running {label}...")
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            check=True,
            capture_output=False,
        )
        log(f"✓ {label} completed.")
        return True
    except subprocess.CalledProcessError as e:
        log(f"✗ {label} failed with exit code {e.returncode}")
        return False
    except Exception as e:
        log(f"✗ Failed to run {label}: {e}")
        return False


def main():
    log("=" * 70)
    log("RNLI Gravitee Platform Initialisation")
    log("=" * 70)

    log("")
    log("STEP 1: Access Management (AM)...")
    if not run_script("/app/am_init.py", "AM Initialisation"):
        log("WARNING: AM initialisation failed — continuing with APIM init anyway.")

    log("")
    log("STEP 2: API Management (APIM)...")
    if not run_script("/app/init.py", "APIM Initialisation"):
        log("FATAL: APIM initialisation failed.")
        sys.exit(1)

    log("")
    log("=" * 70)
    log("✓ All Gravitee services initialised successfully.")
    log("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Interrupted.")
        sys.exit(1)
    except Exception as e:
        log(f"FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
