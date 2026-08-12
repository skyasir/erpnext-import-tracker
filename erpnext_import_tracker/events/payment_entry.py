import frappe
from frappe.utils import flt, get_link_to_form

from erpnext_import_tracker.import_tracker.doctype.import_shipment.import_shipment import (
	create_import_shipment,
)


def on_submit(doc, method=None):
	"""Record supplier payments against the shipment (SOP steps 8-9).

	The advance and balance legs of an import are ordinary Payment Entries, so the
	tracker mirrors them into its Payments table rather than asking accounts to key the
	same figures twice. The SWIFT copy and the UTR hand-off stay manual -- they are the
	proof the SOP gates on, and they arrive from the bank after the entry is made.
	"""
	if doc.payment_type != "Pay":
		return

	for ref in doc.get("references") or []:
		if ref.reference_doctype != "Purchase Order":
			continue

		po = frappe.db.get_value(
			"Purchase Order",
			ref.reference_name,
			[
				"name", "custom_is_import", "supplier", "advance_paid",
				"grand_total", "base_grand_total", "currency", "party_account_currency",
			],
			as_dict=True,
		)
		if not po or not po.custom_is_import:
			continue

		fully_paid = is_fully_paid(po)

		name = frappe.db.exists("Import Shipment", {"purchase_order": po.name})
		if not name:
			# Fallback for POs submitted before the tracker existed: the balance payment
			# opens the shipment. Advance-only payments wait for the PO submit hook.
			if not fully_paid:
				continue
			name = create_import_shipment(po.name)
			frappe.msgprint(
				get_link_to_form("Import Shipment", name),
				title=frappe._("Import Shipment created"),
				indicator="green",
			)

		record_payment(name, doc, ref, po, fully_paid)


def is_fully_paid(po):
	"""Has this order been settled in full?

	advance_paid is summed off the Advance Payment Ledger, so it carries the *party
	account's* currency -- recorded on the order as party_account_currency -- which on an
	import is usually the company's, not the order's. Comparing it against grand_total
	would put an INR figure next to a USD one and read a 30% advance as paid in full.
	"""
	total = (
		flt(po.grand_total)
		if (po.party_account_currency or po.currency) == po.currency
		else flt(po.base_grand_total)
	)
	# 1.0 tolerance absorbs rounding on the final instalment.
	return flt(po.advance_paid) + 1.0 >= total


def record_payment(name, payment_entry, ref, po, fully_paid):
	shipment = frappe.get_doc("Import Shipment", name)

	if any(row.payment_entry == payment_entry.name for row in shipment.payments):
		return

	shipment.append(
		"payments",
		{
			"payment_type": "Balance to Supplier" if fully_paid else "Advance to Supplier",
			"party": po.supplier,
			"amount": flt(ref.allocated_amount) or flt(payment_entry.paid_amount),
			"payment_date": payment_entry.posting_date,
			"utr_no": payment_entry.reference_no,
			"payment_entry": payment_entry.name,
		},
	)

	frappe.db.savepoint("import_tracker_payment")
	try:
		shipment.flags.ignore_permissions = True
		shipment.save()
	except frappe.ValidationError as e:
		frappe.db.rollback(save_point="import_tracker_payment")
		frappe.msgprint(
			frappe._("Could not record this payment on {0}: {1}").format(
				get_link_to_form("Import Shipment", name), str(e)
			),
			title=frappe._("Import Shipment not updated"),
			indicator="orange",
		)
