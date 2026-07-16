import frappe
from frappe.utils import flt


def on_submit(doc, method=None):
	if doc.payment_type != "Pay":
		return

	for ref in doc.get("references") or []:
		if ref.reference_doctype != "Purchase Order":
			continue

		po = frappe.db.get_value(
			"Purchase Order",
			ref.reference_name,
			["name", "custom_is_import", "advance_paid", "grand_total", "supplier"],
			as_dict=True,
		)
		if not po or not po.custom_is_import:
			continue

		if frappe.db.exists("Import Shipment", {"purchase_order": po.name}):
			continue

		# Balance payment = PO now fully paid (advance_paid updated by Payment Entry controller
		# before doc_events fire). 1.0 tolerance for rounding.
		if flt(po.advance_paid) + 1.0 < flt(po.grand_total):
			continue

		shipment = frappe.new_doc("Import Shipment")
		shipment.purchase_order = po.name
		shipment.mode = "Sea"
		shipment.flags.ignore_permissions = True
		shipment.insert()

		frappe.msgprint(
			frappe.utils.get_link_to_form("Import Shipment", shipment.name),
			title="Import Shipment created",
			indicator="green",
		)
