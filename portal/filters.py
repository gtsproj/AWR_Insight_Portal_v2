# portal/filters.py
# Custom Jinja2 filters registered in app.py
import os

def basename(path):
    """Return the filename portion of a path."""
    return os.path.basename(str(path)) if path else ""
