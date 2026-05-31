"""Offline evaluation harness for the Video → SOP pipeline.

Run from the repo root::

    cd backend
    python -m evals.offline.runner --all
    python -m evals.offline.runner --fixtures physical_simple
    python -m evals.offline.runner --skip-llm    # deterministic metrics only

Outputs a CLI table per fixture plus a JSON dump at
``backend/evals/offline/reports/<timestamp>.json`` that can be diffed
between runs.
"""
