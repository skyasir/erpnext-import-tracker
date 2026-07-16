import frappe
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


class ImportShipment(Document):
	def before_insert(self):
		if not self.boe_checklist:
			for doc_name in DEFAULT_CHECKLIST:
				self.append("boe_checklist", {"document_name": doc_name})

	def validate(self):
		self.sync_end_use_row()
		self.stamp_dates()

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

	def before_workflow_action(self):
		"""Block checklist confirmation until every applicable document is received and verified."""
		action = frappe.form_dict.get("action")
		if action == "Confirm Checklist":
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
