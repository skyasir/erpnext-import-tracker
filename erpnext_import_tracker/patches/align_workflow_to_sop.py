"""Re-align the Import Shipment workflow with the 25-step Import Process SOP.

Two things change for installs that predate this patch:

* Four states are added -- Advance Paid / Balance Paid (SOP 8-9) at the front and
  Docs Submitted to Bank / Closed (SOP 25) at the end.
* "Docs Endorsed" moves from after "Under Clearance" to before "BOE Checklist
  Confirmed", because the SOP endorses documents at step 13 and files the BOE at
  step 14 -- the old order had those backwards.

The states and transitions tables are rebuilt wholesale from install.py so the patch
is idempotent and so transitions dropped by the reorder ("Mark Docs Endorsed" out of
Under Clearance, "Mark Cleared" out of Docs Endorsed) actually disappear.

No status is renamed, so existing shipments keep the state they were in.
"""

import frappe

from erpnext_import_tracker.install import (
	STATES,
	TRANSITIONS,
	WORKFLOW_NAME,
	make_custom_fields,
	make_payment_terms,
	make_workflow_masters,
)


def execute():
	make_custom_fields()
	make_payment_terms()
	drop_superseded_custom_fields()
	make_workflow_masters()

	if not frappe.db.exists("Workflow", WORKFLOW_NAME):
		return

	wf = frappe.get_doc("Workflow", WORKFLOW_NAME)

	wf.states = []
	for state, role, _style in STATES:
		wf.append("states", {"state": state, "doc_status": "0", "allow_edit": role})

	wf.transitions = []
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

	wf.save(ignore_permissions=True)

	backfill_stage_index()
	migrate_coo_received()
	seed_plant_notification_settings()


def backfill_stage_index():
	"""Section visibility keys off stage_index, so every existing shipment needs one.

	Rows created before the field existed default to 0, which would hide every section
	on a shipment that is in fact halfway through clearance.
	"""
	order = [state for state, _role, _style in STATES]
	for index, state in enumerate(order):
		frappe.db.set_value(
			"Import Shipment",
			{"status": state, "stage_index": ["!=", index]},
			"stage_index",
			index,
			update_modified=False,
		)


def seed_plant_notification_settings():
	"""Give the new plant-intimation fields their defaults.

	A Single stores only values that have been written, so a field introduced by this
	release reads back as 0 whether or not anyone chose that -- it would look configured
	while the SOP step 23 notification quietly never fired. This patch is the release
	step that introduces the setting, so it establishes the default outright. Anyone who
	wants it off turns it off afterwards; the patch will not run again.
	"""
	settings = frappe.get_single("Import Tracker Settings")
	settings.notify_plant_on_eway_bill = 1
	if not settings.store_notification_role and frappe.db.exists("Role", "Stock User"):
		settings.store_notification_role = "Stock User"
	settings.flags.ignore_permissions = True
	settings.save()


def drop_superseded_custom_fields():
	"""Import payment terms are standard Payment Terms Templates now.

	An earlier cut of this release added a Select on Supplier for them. ERPNext already
	carries a default template per supplier, so the custom field was a second answer to
	a question the platform had answered.
	"""
	for dt, fieldname in (("Supplier", "custom_import_payment_terms"),):
		name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
			print("align_workflow_to_sop: removed %s.%s" % (dt, fieldname))


def migrate_coo_received():
	"""Fold the old coo_received checkbox into the Certificate of Origin document row.

	The checkbox recorded the same fact as the checklist row next to it -- one of the
	duplicate fields this release removes -- so the tick has to land on the row before
	the column stops being read.
	"""
	if not frappe.db.has_column("Import Shipment", "coo_received"):
		return

	ticked = frappe.get_all(
		"Import Shipment", filters={"coo_received": 1}, pluck="name"
	)
	if not ticked:
		return

	rows = frappe.get_all(
		"BOE Checklist Item",
		filters={
			"parent": ["in", ticked],
			"parenttype": "Import Shipment",
			"document_name": "Certificate of Origin",
			"received": 0,
		},
		pluck="name",
	)
	for row in rows:
		frappe.db.set_value("BOE Checklist Item", row, "received", 1, update_modified=False)

	if rows:
		print("align_workflow_to_sop: carried coo_received onto %d document row(s)" % len(rows))
