import frappe
from frappe.utils import get_link_to_form

from erpnext_import_tracker.import_tracker.doctype.import_shipment.import_shipment import (
	create_import_shipment,
)


def validate(doc, method=None):
	flag_import_items(doc)
	set_consignee_address(doc)


def flag_import_items(doc):
	"""Sahil 07-08: an item bought on import should carry its import handling by itself,
	rather than relying on the buyer remembering to tick the box."""
	if doc.get("custom_is_import"):
		return

	item_codes = {item.item_code for item in doc.items if item.item_code}
	if not item_codes:
		return

	if frappe.db.exists(
		"Item", {"name": ["in", list(item_codes)], "custom_is_import_item": 1}
	):
		doc.custom_is_import = 1


def set_consignee_address(doc):
	"""Sahil 07-08: on an import purchase order the consignee is us, and the address the
	overseas vendor consigns to must match our billing address.

	Written into the standard Shipping Address so every existing purchase order print
	format picks it up without a template change.
	"""
	if not doc.get("custom_is_import") or not doc.billing_address:
		return

	if doc.shipping_address != doc.billing_address:
		doc.shipping_address = doc.billing_address
		doc.shipping_address_display = frappe.get_cached_value(
			"Address", doc.billing_address, "address_display"
		) or doc.billing_address_display


def on_submit(doc, method=None):
	"""Auto-create an Import Shipment when an import Purchase Order is submitted."""
	if not doc.get("custom_is_import"):
		return

	if frappe.db.exists("Import Shipment", {"purchase_order": doc.name}):
		return

	name = create_import_shipment(doc.name)
	frappe.msgprint(
		get_link_to_form("Import Shipment", name),
		title=frappe._("Import Shipment created"),
		indicator="green",
	)
