import frappe


def execute():
	"""Add the initial 'Awaiting Shipment' state (+ 'Mark Docs Received' transition) to the
	existing Import Shipment Process workflow. Idempotent."""
	if not frappe.db.exists("Workflow State", "Awaiting Shipment"):
		frappe.get_doc(
			{"doctype": "Workflow State", "workflow_state_name": "Awaiting Shipment", "style": "Warning"}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Workflow Action Master", "Mark Docs Received"):
		frappe.get_doc(
			{"doctype": "Workflow Action Master", "workflow_action_name": "Mark Docs Received"}
		).insert(ignore_permissions=True)

	name = "Import Shipment Process"
	if not frappe.db.exists("Workflow", name):
		return

	wf = frappe.get_doc("Workflow", name)
	changed = False

	if not any(s.state == "Awaiting Shipment" for s in wf.states):
		wf.append("states", {"state": "Awaiting Shipment", "doc_status": "0", "allow_edit": "Purchase User"})
		changed = True

	# "Awaiting Shipment" must be the first state row: Frappe treats states[0] as the
	# initial state for new documents (see frappe.model.workflow.validate_workflow).
	if wf.states and wf.states[0].state != "Awaiting Shipment":
		row = next(s for s in wf.states if s.state == "Awaiting Shipment")
		wf.states.remove(row)
		wf.states.insert(0, row)
		for idx, state in enumerate(wf.states, start=1):
			state.idx = idx
		changed = True

	if not any(t.action == "Mark Docs Received" for t in wf.transitions):
		wf.append(
			"transitions",
			{
				"state": "Awaiting Shipment",
				"action": "Mark Docs Received",
				"next_state": "Docs Received",
				"allowed": "Purchase User",
				"allow_self_approval": 1,
			},
		)
		changed = True

	if changed:
		wf.save(ignore_permissions=True)
