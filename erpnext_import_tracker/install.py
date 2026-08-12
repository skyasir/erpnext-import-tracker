import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

WORKFLOW_NAME = "Import Shipment Process"

# Workflow states in SOP order (Import Process SOP section 5, steps 4-25).
# The SOP step each state closes out is noted so the form layout and the gates in
# import_shipment.py can be kept in step with the document.
STATES = [
	# (state, allow_edit_role, style)
	("Awaiting Shipment", "Purchase User", "Warning"),  # SOP 4-6  rate check .. order confirmation
	("Advance Paid", "Accounts User", "Warning"),  # SOP 7-8  payment initiation, advance
	("Balance Paid", "Accounts User", "Warning"),  # SOP 9    balance payment
	("Docs Received", "Purchase User", "Warning"),  # SOP 11   shipping documents
	("Forwarded to CHA", "Purchase User", "Warning"),  # SOP 10-11 CHA handover
	("Pre-Alert Received", "Purchase User", "Warning"),  # SOP 12   pre-alert
	("Docs Endorsed", "Purchase User", "Info"),  # SOP 13   document endorsement
	("BOE Checklist Confirmed", "Purchase Manager", "Info"),  # SOP 14   BOE checklist
	("Duty Paid", "Accounts User", "Info"),  # SOP 15   customs duty
	("Under Clearance", "Purchase User", "Info"),  # SOP 16-19 clearance, other payments
	("Cleared", "Purchase User", "Success"),  # SOP 20   clearance completed
	("In Transit", "Purchase User", "Success"),  # SOP 21-22 e-way bill, transport
	("Received at Factory", "Purchase User", "Success"),  # SOP 23-24 unloading, GRN
	("Docs Submitted to Bank", "Accounts User", "Success"),  # SOP 25   bank submission
	("Closed", "Accounts User", "Success"),  # SOP 25   bank copy received
]

TRANSITIONS = [
	# (state, action, next_state, allowed_role)
	("Awaiting Shipment", "Mark Advance Paid", "Advance Paid", "Accounts User"),
	# Payment terms without an advance leg (Against BL, Against Readiness, Pay Against
	# BL Copy) skip straight to the balance payment.
	("Awaiting Shipment", "Mark Balance Paid", "Balance Paid", "Accounts User"),
	("Advance Paid", "Mark Balance Paid", "Balance Paid", "Accounts User"),
	# 100% Advance leaves nothing to pay on dispatch, so documents may follow directly.
	("Advance Paid", "Mark Docs Received", "Docs Received", "Purchase User"),
	("Balance Paid", "Mark Docs Received", "Docs Received", "Purchase User"),
	("Docs Received", "Forward to CHA", "Forwarded to CHA", "Purchase User"),
	("Forwarded to CHA", "Mark Pre-Alert Received", "Pre-Alert Received", "Purchase User"),
	("Pre-Alert Received", "Mark Docs Endorsed", "Docs Endorsed", "Purchase User"),
	("Docs Endorsed", "Confirm Checklist", "BOE Checklist Confirmed", "Purchase Manager"),
	("BOE Checklist Confirmed", "Mark Duty Paid", "Duty Paid", "Accounts User"),
	("Duty Paid", "Start Clearance", "Under Clearance", "Purchase User"),
	("Under Clearance", "Mark Cleared", "Cleared", "Purchase User"),
	("Cleared", "Dispatch", "In Transit", "Purchase User"),
	("In Transit", "Receive at Factory", "Received at Factory", "Purchase User"),
	("Received at Factory", "Submit Docs to Bank", "Docs Submitted to Bank", "Accounts User"),
	("Docs Submitted to Bank", "Close Shipment", "Closed", "Accounts User"),
]


# SOP section 3 plus Sahil 10-08. Modelled as standard Payment Terms Templates so the
# purchase order, the supplier default and the shipment all speak the same language.
# (name, advance %, description)
PAYMENT_TERMS = [
	("30% Advance", 30, "30% advance on order confirmation; balance against dispatch."),
	("100% Advance", 100, "Full payment upfront on order confirmation."),
	("Against BL", 0, "Payment released against the Bill of Lading."),
	("Against Readiness", 0, "Payment released against readiness of goods for dispatch."),
	("Pay Against BL Copy", 0, "Payment released against a copy of the Bill of Lading."),
	("Prior To Dispatch", 0, "Payment released before the vendor dispatches."),
]


def after_install():
	make_custom_fields()
	make_payment_terms()
	make_workflow_masters()
	make_workflow()
	frappe.db.commit()


def make_payment_terms():
	"""Create the SOP's payment structures as standard Payment Terms Templates."""
	for name, advance, description in PAYMENT_TERMS:
		if frappe.db.exists("Payment Terms Template", name):
			continue

		if not frappe.db.exists("Payment Term", name):
			frappe.get_doc(
				{
					"doctype": "Payment Term",
					"payment_term_name": name,
					"description": description,
					"invoice_portion": 100,
					"due_date_based_on": "Day(s) after invoice date",
					"credit_days": 0,
				}
			).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Payment Terms Template",
				"template_name": name,
				"terms": [
					{
						"doctype": "Payment Terms Template Detail",
						"payment_term": name,
						"description": description,
						"invoice_portion": 100,
						"due_date_based_on": "Day(s) after invoice date",
						"credit_days": 0,
					}
				],
			}
		).insert(ignore_permissions=True)


def make_custom_fields():
	create_custom_fields(
		{
			"Purchase Order": [
				{
					"fieldname": "custom_is_import",
					"label": "Is Import",
					"fieldtype": "Check",
					"insert_after": "supplier",
					"default": "0",
					"module": "Import Tracker",
				}
			],
			# Sahil 07-08: an import item names the supplier it is bought from, so the
			# purchase order fills the vendor in by itself.
			"Item": [
				{
					"fieldname": "custom_is_import_item",
					"label": "Is Import Item",
					"fieldtype": "Check",
					"insert_after": "is_purchase_item",
					"default": "0",
					"description": "Requires a Default Supplier, and marks purchase orders as imports.",
					"module": "Import Tracker",
				}
			],
		},
		ignore_validate=True,
	)


def make_workflow_masters():
	for state, _role, style in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)

	for _state, action, _next, _role in TRANSITIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc(
				{"doctype": "Workflow Action Master", "workflow_action_name": action}
			).insert(ignore_permissions=True)


def make_workflow():
	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		return

	wf = frappe.new_doc("Workflow")
	wf.workflow_name = WORKFLOW_NAME
	wf.document_type = "Import Shipment"
	wf.workflow_state_field = "status"
	wf.is_active = 1
	wf.send_email_alert = 0

	# states[0] is the initial state for new documents, so "Awaiting Shipment" must
	# stay first (frappe.model.workflow.validate_workflow).
	for state, role, _style in STATES:
		wf.append("states", {"state": state, "doc_status": "0", "allow_edit": role})

	for state, action, next_state, role in TRANSITIONS:
		wf.append(
			"transitions",
			{
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": role,
				"allow_self_approval": 1,
			},
		)

	wf.insert(ignore_permissions=True)
