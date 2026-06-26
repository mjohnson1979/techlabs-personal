"""Deployment entry point.

App Service / Oryx runs gunicorn from the extracted app root. The real FastAPI
application lives in the ``server`` package; this shim puts ``server`` on the
import path and re-exports ``app`` so the startup command can simply target
``main:app`` without depending on a working-directory ``--chdir``.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))

from app import app  # noqa: E402,F401
