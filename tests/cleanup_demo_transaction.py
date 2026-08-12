"""Remove the demo transaction created by create_live_transaction.py.

Cancels and deletes the goods receipt, the shipment and the purchase order, newest
document first so the links come apart cleanly. Only touches shipments whose remarks
carry the IMPORT-SOP-DEMO tag, and the purchase order / receipt reachable from them.

Run:  cd <bench>/sites && ../env/bin/python ../apps/erpnext_import_tracker/tests/cleanup_demo_transaction.py
      add --commit to actually delete; without it the script only reports.
"""

import sys

import frappe

frappe.init(site="supreme.localhost")
frappe.connect()
frappe.set_user("Administrator")

COMMIT = "--commit" in sys.argv
TAG = "IMPORT-SOP-DEMO"

shipments = frappe.get_all(
	"Import Shipment", filters={"remarks": ["like", "%" + TAG + "%"]},
	fields=["name", "status", "purchase_order", "purchase_receipt"],
)

if not shipments:
	print("nothing tagged %s on this site" % TAG)
	raise SystemExit(0)

print("%s:" % ("DELETING" if COMMIT else "would delete (dry run, pass --commit)"))
for s in shipments:
	print("  Import Shipment  %s  (%s)" % (s.name, s.status))
	if s.purchase_receipt:
		print("  Purchase Receipt %s" % s.purchase_receipt)
	if s.purchase_order:
		print("  Purchase Order   %s" % s.purchase_order)

if not COMMIT:
	raise SystemExit(0)


def drop(doctype, name):
	if not name or not frappe.db.exists(doctype, name):
		return
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus == 1:
		doc.flags.ignore_permissions = True
		doc.cancel()
	frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
	print("  deleted %s %s" % (doctype, name))


for s in shipments:
	drop("Purchase Receipt", s.purchase_receipt)
	frappe.delete_doc("Import Shipment", s.name, force=True, ignore_permissions=True)
	print("  deleted Import Shipment %s" % s.name)

	if s.purchase_order:
		for pe in frappe.get_all(
			"Payment Entry Reference",
			filters={"reference_doctype": "Purchase Order", "reference_name": s.purchase_order},
			pluck="parent",
		):
			drop("Payment Entry", pe)
		drop("Purchase Order", s.purchase_order)

frappe.db.commit()
print("done")
