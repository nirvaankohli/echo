"""Compatibility launcher; new commands use python -m echo."""
import runpy

if __name__ == "__main__":
    runpy.run_module("echo.runtime.run_echo", run_name="__main__")
