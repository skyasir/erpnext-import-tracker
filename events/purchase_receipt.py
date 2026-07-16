import frappe
from frappe.utils import today


def on_submit(doc, method=None):
	pos = {item.purchase_order for item in doc.items if item.purchase_order}
	if not pos:
		return

	shipments = frappe.get_all(
		"Import Shipment",
		filters={"purchase_order": ["in", list(pos)], "status": ["!=", "Received at Factory"]},
		pluck="name",
	)
	for name in shipments:
		frappe.db.set_value(
			"Import Shipment",
			name,
			{"status": "Received at Factory", "received_date": doc.posting_date or today()},
		)
		frappe.msgprint(
			frappe.utils.get_link_to_form("Import Shipment", name),
			title="Import Shipment marked Received at Factory",
			indicator="green",
		)
