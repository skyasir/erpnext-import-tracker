import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

DEFAULT_CHECKLIST = [
	"Commercial Invoice",
	"Packing List",
	"BL / AWB",
	"Certificate of Origin",
	"Insurance",
	"Purchase Order Copy",
]

# States before the checklist is confirmed; the checklist need not be complete in these.
# Every later state requires all applicable documents received and verified.
PRE_CHECKLIST_STATES = (
	"Awaiting Shipment",
	"Docs Received",
	"Forwarded to CHA",
	"Pre-Alert Received",
)


class ImportShipment(Document):
	def before_insert(self):
		if not self.boe_checklist:
			for doc_name in DEFAULT_CHECKLIST:
				self.append("boe_checklist", {"document_name": doc_name})

	def validate(self):
		self.sync_end_use_row()
		self.stamp_dates()
		self.validate_checklist_complete()

	def sync_end_use_row(self):
		"""Add/remove the conditional End Use Declaration row (dashed box in the flowchart)."""
		has_row = any(d.document_name == "End Use Declaration" for d in self.boe_checklist)
		if self.end_use_declaration_required and not has_row:
			self.append("boe_checklist", {"document_name": "End Use Declaration"})
		elif not self.end_use_declaration_required and has_row:
			self.boe_checklist = [
				d for d in self.boe_checklist if d.document_name != "End Use Declaration"
			]
			for idx, d in enumerate(self.boe_checklist, start=1):
				d.idx = idx

	def stamp_dates(self):
		if self.status == "Duty Paid" and not self.duty_paid_date:
			self.duty_paid_date = today()
		if self.status == "Cleared" and not self.clearance_date:
			self.clearance_date = today()
		if self.status == "Received at Factory" and not self.received_date:
			self.received_date = today()

	def validate_checklist_complete(self):
		"""Block checklist confirmation (and every later state) until every applicable
		document is received and verified.

		Enforced in validate() because each workflow transition saves the document.
		before_workflow_action is a client-side form event, not a server hook, so a
		Python method of that name never runs on the server.
		"""
		if self.status in PRE_CHECKLIST_STATES:
			return

		pending = [
			d.document_name
			for d in self.boe_checklist
			if not (d.received and d.verified)
		]
		if pending:
			frappe.throw(
				"Cannot confirm BOE checklist. Pending documents: {0}".format(
					", ".join(pending)
				)
			)


def create_import_shipment(purchase_order):
	"""Create an Import Shipment for a PO, or return the existing one.

	Internal helper (no permission check) shared by the Purchase Order / Payment Entry
	auto-create hooks. Callers that run on user action should permission-check first.
	"""
	existing = frappe.db.exists("Import Shipment", {"purchase_order": purchase_order})
	if existing:
		return existing

	shipment = frappe.new_doc("Import Shipment")
	shipment.purchase_order = purchase_order
	shipment.mode = "Sea"
	shipment.insert(ignore_permissions=True)
	return shipment.name


@frappe.whitelist()
def make_import_shipment(purchase_order):
	"""Create (or open the existing) Import Shipment for an import PO. Button entry point."""
	po = frappe.db.get_value(
		"Purchase Order",
		purchase_order,
		["name", "custom_is_import", "docstatus"],
		as_dict=True,
	)
	if not po:
		frappe.throw(_("Purchase Order {0} not found").format(purchase_order))
	if not po.custom_is_import:
		frappe.throw(_("{0} is not marked as an import Purchase Order").format(purchase_order))
	if po.docstatus != 1:
		frappe.throw(_("Purchase Order {0} must be submitted first").format(purchase_order))

	if not frappe.db.exists("Import Shipment", {"purchase_order": purchase_order}):
		frappe.has_permission("Import Shipment", "create", throw=True)

	return create_import_shipment(purchase_order)
