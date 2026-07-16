import frappe


def on_submit(boe, method=None):
	"""Link a submitted (standard India Compliance) Bill of Entry back to its Import
	Shipment and stamp the duty date.

	A Bill of Entry references its Purchase Invoice(s) at the item level. A shipment may
	link the PI directly, or only the PO (when it was auto-created at PO submit before the
	invoice existed), so match on both.
	"""
	purchase_invoices = {row.purchase_invoice for row in boe.get("items", []) if row.get("purchase_invoice")}
	if not purchase_invoices:
		return

	shipments = set(
		frappe.get_all(
			"Import Shipment",
			filters={"purchase_invoice": ["in", list(purchase_invoices)]},
			pluck="name",
		)
	)

	purchase_orders = set(
		frappe.get_all(
			"Purchase Invoice Item",
			filters={"parent": ["in", list(purchase_invoices)], "purchase_order": ["is", "set"]},
			pluck="purchase_order",
		)
	)
	if purchase_orders:
		shipments |= set(
			frappe.get_all(
				"Import Shipment",
				filters={"purchase_order": ["in", list(purchase_orders)]},
				pluck="name",
			)
		)

	pi_fallback = next(iter(purchase_invoices))
	for name in shipments:
		current = frappe.db.get_value(
			"Import Shipment", name, ["duty_paid_date", "purchase_invoice"], as_dict=True
		)
		updates = {"bill_of_entry": boe.name}
		if not current.duty_paid_date:
			updates["duty_paid_date"] = boe.bill_of_entry_date
		if not current.purchase_invoice:
			updates["purchase_invoice"] = pi_fallback
		frappe.db.set_value("Import Shipment", name, updates)
		frappe.msgprint(
			frappe.utils.get_link_to_form("Import Shipment", name),
			title="Bill of Entry linked to Import Shipment",
			indicator="green",
		)
