#!/bin/bash
cd "$(dirname "$0")"
# Launch as a foreground GUI so macOS delivers mouse clicks to Tk.
exec .venv/bin/python app.py
