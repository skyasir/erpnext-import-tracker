import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

STATES = [
	# (state, allow_edit_role, style)
	("Docs Received", "Purchase User", "Warning"),
	("Forwarded to CHA", "Purchase User", "Warning"),
	("Pre-Alert Received", "Purchase User", "Warning"),
	("BOE Checklist Confirmed", "Purchase Manager", "Info"),
	("Duty Paid", "Accounts User", "Info"),
	("Under Clearance", "Purchase User", "Info"),
	("Docs Endorsed", "Purchase User", "Info"),
	("Cleared", "Purchase User", "Success"),
	("In Transit", "Purchase User", "Success"),
	("Received at Factory", "Purchase User", "Success"),
]

TRANSITIONS = [
	# (state, action, next_state, allowed_role)
	("Docs Received", "Forward to CHA", "Forwarded to CHA", "Purchase User"),
	("Forwarded to CHA", "Mark Pre-Alert Received", "Pre-Alert Received", "Purchase User"),
	("Pre-Alert Received", "Confirm Checklist", "BOE Checklist Confirmed", "Purchase Manager"),
	("BOE Checklist Confirmed", "Mark Duty Paid", "Duty Paid", "Accounts User"),
	("Duty Paid", "Start Clearance", "Under Clearance", "Purchase User"),
	("Under Clearance", "Mark Docs Endorsed", "Docs Endorsed", "Purchase User"),
	("Docs Endorsed", "Mark Cleared", "Cleared", "Purchase User"),
	("Cleared", "Dispatch", "In Transit", "Purchase User"),
	("In Transit", "Receive at Factory", "Received at Factory", "Purchase User"),
]


def after_install():
	make_custom_fields()
	make_workflow_masters()
	make_workflow()
	frappe.db.commit()


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
			]
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
	name = "Import Shipment Process"
	if frappe.db.exists("Workflow", name):
		return

	wf = frappe.new_doc("Workflow")
	wf.workflow_name = name
	wf.document_type = "Import Shipment"
	wf.workflow_state_field = "status"
	wf.is_active = 1
	wf.send_email_alert = 0

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
