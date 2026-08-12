"""AppTest verification for the 4-page Streamlit app.

Run from the project root:

    .venv/bin/python tests/apptest_pages.py

Verifies that every page runs without exceptions, the export section shows
exactly one download button per flow, and the Dashboard shows the count
metrics + per-currency chart/table (with a seeded Supabase client).
"""

from __future__ import annotations

import sys

ROOT = "/home/svinoshan/projects/invoice/invoice_converter"
sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

fails = []


def run(page, label):
    try:
        at = AppTest.from_file(f"{ROOT}/app.py", default_timeout=20)
        at.run()
        if at.exception:
            fails.append(f"{label}: app.py {[str(e) for e in at.exception]}")
            return
        at.switch_page(page)
        at.run()
        if at.exception:
            fails.append(f"{label}: {[str(e) for e in at.exception]}")
            return
        print(f"ok  {label}")
    except Exception as exc:  # noqa: BLE001
        fails.append(f"{label}: {exc}")


run("pages/dashboard.py", "dashboard")
run("pages/upload_process.py", "upload_process")
run("pages/saved_records.py", "saved_records")
run("pages/settings.py", "settings")

# Saved Records: Save/Export buttons sit ABOVE the table; Save is disabled
# until an edit marks state as pending (same pattern as Upload & Process).
# Records auto-load and auto-refetch on filter changes (no Load button).
from streamlit.testing.v1.element_tree import Block  # noqa: E402


def _ordered_types(root):
    """Walk the element tree in document order, collecting element type names."""
    out = []
    for _eid, nodes in root.children.items():
        for node in nodes:
            out.append(type(node).__name__.lower())
            if isinstance(node, Block):
                out.extend(_ordered_types(node))
    return out


at = AppTest.from_file(f"{ROOT}/app.py", default_timeout=20)
at.run()
at.switch_page("pages/saved_records.py")
at.run()
assert not at.exception, [str(e) for e in at.exception]
buttons = [b.label for b in at.get("button")]
print("saved_records buttons:", buttons)
assert "Save changes" in buttons, buttons
assert "Load saved records" not in buttons, buttons
assert "Export to Excel" not in buttons, buttons
downloads = [b.label for b in at.get("download_button")]
print("saved_records downloads:", downloads)
assert "Export to Excel" in downloads, downloads
save_button = [b for b in at.get("button") if b.label == "Save changes"][0]
assert save_button.disabled, "Save changes should start disabled"
captions = [c.value for c in at.get("caption")]
assert ":green[All changes saved]" in captions, captions
order = _ordered_types(at.main)
print("types:", [t for t in order if t in ("button", "data_editor", "caption", "dataframe")])
assert order.index("button") < order.index("dataframe"), "buttons above table"
# An edit marks the state as pending: button enables, caption flips to red.
at.session_state["saved_edits"] = True
at.run()
save_button = [b for b in at.get("button") if b.label == "Save changes"][0]
assert not save_button.disabled, "Save changes should be enabled after an edit"
assert ":red[Unsaved changes]" in [c.value for c in at.get("caption")], "caption"

# Export section: exactly one download button per flow on the upload page.
at = AppTest.from_file(f"{ROOT}/app.py", default_timeout=20)
at.run()
at.switch_page("pages/upload_process.py")
at.run()
downloads = [b.label for b in at.get("download_button")]
print("download buttons:", downloads)
assert "Export all records to Excel" in downloads, downloads
assert "Export current extracted rows to Excel (preview)" in downloads, downloads
assert downloads.count("Export all records to Excel") == 1, downloads
assert downloads.count("Export current extracted rows to Excel (preview)") == 1, downloads

# Dashboard: count metrics only, no legacy summed-currency cards.
at.switch_page("pages/dashboard.py")
at.run()
metrics = [m.label for m in at.metric]
print("metrics:", metrics)
for label in ("Total saved records", "Needs review", "Reviewed", "PDFs processed"):
    assert label in metrics, metrics
assert "Sum of Sub total" not in metrics and "Sum of Total amount" not in metrics, metrics

# Seed Supabase so the per-currency chart/table render.
from services import supabase_service  # noqa: E402
from utils import ui_helpers  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.state = {
            "fake_data": [
                {"client": "M", "invoice_no": "I-1", "invoice_date": "2026-06-08",
                 "description": "d", "sub_total": 1345.2, "total_amount": 1345.2,
                 "currency": "Euro", "extraction_status": "processed",
                 "reviewed": True, "source_file_name": "a.pdf"},
                {"client": "B", "invoice_no": "I-2", "invoice_date": "2026-06-09",
                 "description": "d2", "sub_total": 53.1, "total_amount": 53.1,
                 "currency": "USD", "extraction_status": "needs_review",
                 "reviewed": False, "source_file_name": "b.pdf"},
                {"client": "M", "invoice_no": "I-3", "invoice_date": "2026-06-10",
                 "description": "d3", "sub_total": 1345.2, "total_amount": 1345.2,
                 "currency": "Euro", "extraction_status": "processed",
                 "reviewed": False, "source_file_name": "a.pdf"},
            ]
        }

    def table(self, name):
        assert name == "invoice_records", name
        return _FakeTable(self.state)


class _FakeTable:
    def __init__(self, state):
        self.state = state

    def select(self, *args, **kwargs):
        return _FakeQuery(self.state).select(*args, **kwargs)

    def delete(self):
        return _FakeQuery(self.state).delete()


class _FakeQuery:
    def __init__(self, state):
        self.state = state

    def select(self, *args, **kwargs):
        return self

    def eq(self, *a): return self
    def ilike(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def range(self, *a): return self
    def delete(self): return self

    def execute(self):
        return type("R", (), {"data": self.state["fake_data"], "count": None})()


ui_helpers.cached_summary.clear()
ui_helpers.cached_export_bytes_from_db.clear()
supabase_service._client = _FakeClient()  # noqa: SLF001
supabase_service.is_enabled = lambda: True
supabase_service.is_configured = lambda: True
at.run()
assert not at.exception, [str(e) for e in at.exception]
assert (
    at.get("bar_chart") or at.get("arrow_vega_lite_chart") or at.get("vega_lite_chart")
), "no bar chart"
assert at.dataframe, "no currency table"

# Saved Records with seeded data: the duplicate-cleanup expander lists the
# two rows sharing a.pdf and offers a Delete button per row.
at.switch_page("pages/saved_records.py")
at.run()
assert not at.exception, [str(e) for e in at.exception]
buttons = [b.label for b in at.get("button")]
print("saved_records seeded buttons:", buttons)
assert "Delete" in buttons, buttons
assert "No duplicate source files found." not in [
    c.value for c in at.get("caption")
], "seeded duplicate group must be listed"
assert "I-1" in [c.value for c in at.get("caption")], "duplicate invoice no shown"

print("FAILS:", fails) if fails else print("all apptest checks passed")
sys.exit(1 if fails else 0)
