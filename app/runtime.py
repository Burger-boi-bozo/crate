"""Stable import path for the active Crate runtime."""
from app.runtime_v5 import app, create_app

__all__ = ["app", "create_app"]
