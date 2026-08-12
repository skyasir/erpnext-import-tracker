"""Prove the progressive-disclosure rules can't deadlock the workflow.

Section visibility keys off stage_index. For every gate, the field it demands must sit
in a section that is already visible at the stage the user takes the action FROM
(target index - 1) -- otherwise validate() asks for a field the form is hiding.

Run:  cd <bench> && env/bin/python apps/erpnext_import_tracker/tests/test_section_visibility.py
"""

import frappe

frappe.init(site="supreme.localhost")
frappe.connect()

from erpnext_import_tracker.import_tracker.doctype.import_shipment.import_shipment import (  # noqa: E402
	STATE_ORDER,
)

PASS, FAIL = [], []


def check(label, cond, detail=""):
	(PASS if cond else FAIL).append(label)
	print("%s  %s%s" % ("PASS" if cond else "FAIL", label, (" -- " + str(detail)) if detail else ""))


# ---------------------------------------------------------------- map the form
meta = frappe.get_meta("Import Shipment")
field_section = {}
section_threshold = {}
current_section = None

for df in meta.fields:
	if df.fieldtype == "Section Break":
		current_section = df.fieldname
		dep = df.depends_on or ""
		if "stage_index >=" in dep:
			section_threshold[current_section] = int(
				dep.split("stage_index >=")[1].strip().split()[0].rstrip("\"'&")
			)
		else:
			section_threshold[current_section] = 0  # always visible (or terms-based)
	elif current_section:
		field_section[df.fieldname] = current_section

print("=== section reveal thresholds ===")
for sec, idx in section_threshold.items():
	print("  %-30s stage %-2d (%s)" % (sec, idx, "always" if idx == 0 else STATE_ORDER[idx]))

# ------------------------------------------------- what each gate demands
# target state -> fields validate() requires in order to reach it
GATE_FIELDS = {
	"Advance Paid": ["payment_terms_template", "incoterm", "payments"],
	"Balance Paid": ["payments", "pi_value_final", "pi_variation_reason"],
	"Docs Received": ["boe_checklist"],
	"Forwarded to CHA": ["cha"],
	"Pre-Alert Received": ["pre_alert_date"],
	"Docs Endorsed": ["original_docs_received", "endorsed_on_soft_copy", "docs_endorsed_date"],
	"BOE Checklist Confirmed": ["boe_checklist"],
	"Duty Paid": ["payments"],
	"Cleared": ["boe_checklist", "payments"],
	"In Transit": ["eway_bill_no", "eway_bill_date", "eway_bill_copy"],
	"Received at Factory": ["unloading_completed", "purchase_receipt"],
	"Docs Submitted to Bank": [
		"purchase_invoice", "bill_of_entry", "bl_awb_no", "covering_letter",
		"docs_submitted_to_bank",
	],
	"Closed": ["bank_received_copy", "bank_received_date"],
}

print("\n=== gate fields are visible before the gate fires ===")
for state, fields in GATE_FIELDS.items():
	target = STATE_ORDER.index(state)
	acting_from = target - 1
	for fieldname in fields:
		section = field_section.get(fieldname)
		if not section:
			check("%s: %s is on the form" % (state, fieldname), False, "field not found")
			continue
		threshold = section_threshold.get(section, 0)
		check(
			"%s needs %s (in %s, shown at %d), acting from stage %d"
			% (state, fieldname, section, threshold, acting_from),
			threshold <= acting_from,
			"section reveals too late" if threshold > acting_from else "",
		)

# ------------------------------------------------- workflow matches STATE_ORDER
print("\n=== workflow matches STATE_ORDER ===")
wf = frappe.get_doc("Workflow", "Import Shipment Process")
wf_states = [s.state for s in wf.states]
check("workflow states match STATE_ORDER", wf_states == STATE_ORDER, wf_states)
check("initial state is Awaiting Shipment", wf_states[0] == "Awaiting Shipment", wf_states[0])

reachable = {wf_states[0]}
for t in wf.transitions:
	if t.state in reachable:
		reachable.add(t.next_state)
# walk repeatedly so ordering of the transition rows does not matter
for _ in range(len(wf.transitions)):
	for t in wf.transitions:
		if t.state in reachable:
			reachable.add(t.next_state)
check(
	"every state is reachable from the initial state",
	set(wf_states) == reachable,
	set(wf_states) - reachable,
)

statuses = frappe.get_meta("Import Shipment").get_field("status").options.split("\n")
check("status options match STATE_ORDER", statuses == STATE_ORDER, statuses)

# ------------------------------------------------- live: stage_index tracks status
print("\n=== live: stage_index follows status ===")
frappe.set_user("Administrator")
po = frappe.db.get_value("Purchase Order", {"docstatus": 1}, "name")
if po:
	ship = frappe.new_doc("Import Shipment")
	ship.purchase_order = po
	ship.mode = "Sea"
	ship.insert(ignore_permissions=True)

	check("new shipment starts at stage 0", ship.stage_index == 0, "%s / %s" % (ship.status, ship.stage_index))
	check(
		"mandatory documents are seeded",
		sorted(d.document_name for d in ship.boe_checklist if d.is_mandatory)
		== sorted(["BL / AWB", "Certificate of Origin", "Commercial Invoice", "Insurance",
		           "Packing List", "Purchase Order Copy"]),
		[d.document_name for d in ship.boe_checklist],
	)

	ship.status = "Docs Received"
	try:
		ship.save()
		check("gate blocks Docs Received without documents", False, "save succeeded")
	except frappe.ValidationError as e:
		check("gate blocks Docs Received without documents", True, str(e)[:60])

	frappe.db.rollback()
else:
	print("  (skipped: no submitted Purchase Order on this site)")

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
	for f in FAIL:
		print("  FAILED: %s" % f)
	raise SystemExit(1)
