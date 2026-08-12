import frappe
from frappe.utils import get_link_to_form, today


def on_submit(doc, method=None):
	"""SOP steps 23-24: the GRN closes out unloading and receipt at the factory.

	The shipment is advanced through the document API so the stage gates still run. A
	tracker that cannot satisfy them must never block the GRN itself, so each shipment
	is advanced inside its own savepoint and a failure is reported, not raised.
	"""
	pos = {item.purchase_order for item in doc.items if item.purchase_order}
	if not pos:
		return

	shipments = frappe.get_all(
		"Import Shipment",
		filters={"purchase_order": ["in", list(pos)], "status": ["!=", "Closed"]},
		pluck="name",
	)
	for name in shipments:
		advance_to_received(name, doc)


def advance_to_received(name, receipt):
	shipment = frappe.get_doc("Import Shipment", name)
	if shipment.status == "Received at Factory":
		return

	frappe.db.savepoint("import_tracker_grn")
	try:
		shipment.purchase_receipt = receipt.name
		shipment.unloading_completed = 1
		shipment.received_date = shipment.received_date or receipt.posting_date or today()
		if not shipment.container_arrival_date:
			shipment.container_arrival_date = receipt.posting_date or today()
		shipment.status = "Received at Factory"
		shipment.flags.ignore_permissions = True
		shipment.save()
	except frappe.ValidationError as e:
		frappe.db.rollback(save_point="import_tracker_grn")
		frappe.msgprint(
			frappe._("{0} could not be marked Received at Factory: {1}").format(
				get_link_to_form("Import Shipment", name), str(e)
			),
			title=frappe._("Import Shipment not updated"),
			indicator="orange",
		)
		return

	frappe.msgprint(
		get_link_to_form("Import Shipment", name),
		title=frappe._("Import Shipment marked Received at Factory"),
		indicator="green",
	)
