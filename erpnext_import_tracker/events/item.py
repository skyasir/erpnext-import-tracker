import frappe
from frappe import _


def validate(doc, method=None):
	"""Sahil 07-08: "for import item Supplier set By Default".

	Imports come from a short list of approved overseas vendors (SOP section 2), so an
	item flagged as an import item must name the supplier it is bought from. ERPNext
	already carries that on Item Default, and pulls it onto the purchase order for us --
	this only makes it non-optional for import items.
	"""
	if not doc.get("custom_is_import_item"):
		return

	if any(row.default_supplier for row in doc.get("item_defaults") or []):
		return

	frappe.throw(
		_("{0} is marked as an import item, so it needs a Default Supplier. "
		  "Set one under Purchasing, Manufacturing & Accounting Defaults.").format(
			doc.item_code or doc.name
		)
	)
