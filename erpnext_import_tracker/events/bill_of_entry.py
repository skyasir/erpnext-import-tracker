import frappe
from frappe.utils import flt, get_link_to_form


def on_submit(boe, method=None):
	"""Link a submitted (standard India Compliance) Bill of Entry back to its Import
	Shipment, stamp the duty date, and seed the customs duty payment row.

	A Bill of Entry references its Purchase Invoice(s) at the item level. A shipment may
	link the PI directly, or only the PO (when it was auto-created at PO submit before the
	invoice existed), so match on both.
	"""
	purchase_invoices = {
		row.purchase_invoice for row in boe.get("items", []) if row.get("purchase_invoice")
	}
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
		link_bill_of_entry(name, boe, pi_fallback)


def link_bill_of_entry(name, boe, pi_fallback):
	current = frappe.db.get_value(
		"Import Shipment", name, ["duty_paid_date", "purchase_invoice", "bl_awb_no"], as_dict=True
	)

	updates = {"bill_of_entry": boe.name}
	if not current.duty_paid_date:
		updates["duty_paid_date"] = boe.bill_of_entry_date
	if not current.purchase_invoice:
		updates["purchase_invoice"] = pi_fallback
	if not current.bl_awb_no and boe.get("bill_of_lading_no"):
		updates["bl_awb_no"] = boe.bill_of_lading_no

	# db_set rather than a full save: this runs during BOE submit and must not fail on a
	# tracker gate that has nothing to do with the Bill of Entry.
	frappe.db.set_value("Import Shipment", name, updates)

	seed_duty_payment(name, boe)

	frappe.msgprint(
		get_link_to_form("Import Shipment", name),
		title=frappe._("Bill of Entry linked to Import Shipment"),
		indicator="green",
	)


def seed_duty_payment(name, boe):
	"""SOP step 15: the duty amount comes off the BOE; accounts add the challan and the
	UTR they send back to the CHA. Seeded here so the figure is not retyped."""
	amount = flt(boe.get("total_amount_payable")) or flt(boe.get("total_customs_duty"))
	if not amount:
		return

	exists = frappe.db.exists(
		"Import Shipment Payment",
		{"parent": name, "payment_type": "Customs Duty", "remarks": boe.name},
	)
	if exists:
		return

	shipment = frappe.get_doc("Import Shipment", name)
	shipment.append(
		"payments",
		{
			"payment_type": "Customs Duty",
			"amount": amount,
			"payment_date": boe.bill_of_entry_date,
			"remarks": boe.name,
		},
	)

	frappe.db.savepoint("import_tracker_boe")
	try:
		shipment.flags.ignore_permissions = True
		shipment.save()
	except frappe.ValidationError:
		frappe.db.rollback(save_point="import_tracker_boe")
