"""
Vercel serverless entry point.
Imports the FastAPI app from main.py (one level up).
"""
import sys
import os

# Add parent directory to path so all modules are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app  # noqa: F401 — Vercel detects the `app` symbol
