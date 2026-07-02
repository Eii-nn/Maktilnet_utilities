#!/usr/bin/env python3
"""
preflight_integrated2.py
MikroTik Auto-Preflight Configuration Tool v5.1
Refactored for modularity, readability, and testability.
"""

import tkinter as tk
from preflight_modules.ui import PreflightApp

if __name__ == "__main__":
    root = tk.Tk()
    app = PreflightApp(root)
    root.mainloop()
