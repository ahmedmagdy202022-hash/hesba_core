"""R1: the demo pharmacy's reference drug list (``demo_data/drug_reference_demo.csv``, illustrative prices).

Loaded only into the pharmacy demo template while it is built (``config.demo_templates``), through the same importer
as ``manage.py import_drug_reference``; it writes ``DrugReference`` rows only.
"""

from pathlib import Path

DEMO_FILE = Path(__file__).resolve().parent / "demo_data" / "drug_reference_demo.csv"
DEMO_SOURCE = "demo"
DEMO_VERSION = "demo-2026-11"


def load_demo_reference():
    """Import the demo list under the ``demo`` source. Returns the importer's summary; running it again creates nothing."""

    from .reference import import_file

    return import_file(DEMO_FILE, source=DEMO_SOURCE, source_version=DEMO_VERSION, layout="hesba")
