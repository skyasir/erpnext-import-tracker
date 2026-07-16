import frappe

from erpnext_import_tracker.import_tracker.doctype.import_shipment.import_shipment import (
	create_import_shipment,
)


def on_submit(doc, method=None):
	"""Auto-create an Import Shipment when an import Purchase Order is submitted."""
	if not doc.get("custom_is_import"):
		return

	if frappe.db.exists("Import Shipment", {"purchase_order": doc.name}):
		return

	name = create_import_shipment(doc.name)
	frappe.msgprint(
		frappe.utils.get_link_to_form("Import Shipment", name),
		title="Import Shipment created",
		indicator="green",
	)
