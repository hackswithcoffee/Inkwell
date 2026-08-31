#!/usr/bin/env python3
"""Command-line entry point for the Inkwell pipeline.

The implementation lives in the ``inkwell`` package; this stays at the repo
root because the launchd watcher, the docs, and years of muscle memory all
invoke it by this name.
"""
from inkwell.pipeline import run_pipeline

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inkwell Scribe Pipeline")
    parser.add_argument("--date", default=None, help="Session date as MM_DD_YYYY (defaults to today)")
    args = parser.parse_args()
    run_pipeline(session_date=args.date)
