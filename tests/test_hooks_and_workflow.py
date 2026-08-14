"""Exercise the doc_events hooks and the real workflow engine.

uat_sop_flow.py drives the Import Shipment controller directly. This covers what that
misses: the six hooks that fire from other doctypes, and workflow transitions taken
through frappe.model.workflow.apply_workflow rather than by assigning status.

Rollback-safe -- nothing is committed.

Run:  cd <bench>/sites && ../env/bin/python ../apps/erpnext_import_tracker/tests/test_hooks_and_workflow.py
"""

import frappe
from frappe.model.workflow import apply_workflow

from erpnext_import_tracker.import_tracker.doctype.import_shipment.import_shipment import (
	create_import_shipment as create_shipment,
)

frappe.init(site="supreme.localhost")
frappe.connect()
frappe.set_user("Administrator")
frappe.flags.in_test = True

SENT = []
frappe.sendmail = lambda **kwargs: SENT.append(kwargs)

PASS, FAIL = [], []


def check(label, cond, detail=""):
	(PASS if cond else FAIL).append(label)
	print("%s  %s%s" % ("PASS" if cond else "FAIL", label, (" -- " + str(detail)) if detail else ""))


def blocked(label, fn, expect=None):
	try:
		fn()
	except frappe.ValidationError as e:
		msg = str(e)
		check(label, expect.lower() in msg.lower() if expect else True, msg[:120])
		return
	check(label, False, "was allowed through")


# A foreign-currency order, because every import is one and the advance/balance split
# is currency-sensitive.
TEMPLATE = frappe.db.get_value("Purchase Order", {"docstatus": 1, "currency": ["!=", "INR"]},
                               ["name", "supplier", "company", "currency"], as_dict=True)
# A GST-registered domestic order for the goods-receipt leg: this site's GST rules reject
# a receipt against a supplier with no GSTIN, which has nothing to do with the tracker.
GST_TEMPLATE = frappe.db.sql("""
	SELECT po.name FROM `tabPurchase Order` po JOIN tabSupplier s ON s.name = po.supplier
	WHERE po.docstatus = 1 AND s.gstin IS NOT NULL AND s.gstin != ''
	ORDER BY po.creation DESC LIMIT 1""", pluck=True)[0]

LINE = frappe.get_all("Purchase Order Item", filters={"parent": TEMPLATE.name},
                      fields=["item_code", "warehouse", "uom", "conversion_factor"], limit=1)[0]

# The supplier's default template would otherwise backfill terms the gate is meant to
# demand. Rolled back with everything else.
frappe.db.set_value("Supplier", TEMPLATE.supplier, "payment_terms", None)


def make_po(is_import=0, billing_address=None, source=None):
	"""Clone a known-good submitted order.

	Building one from scratch trips India Compliance's reverse-charge account check on
	this site's tax templates, which has nothing to do with what is under test here.
	"""
	po = frappe.copy_doc(frappe.get_doc("Purchase Order", source or TEMPLATE.name))
	# This site runs a workflow on Purchase Order too; a clone must start from its
	# initial state rather than inheriting the original's.
	po.workflow_state = None
	po.schedule_date = frappe.utils.today()
	po.transaction_date = frappe.utils.today()
	# The source order is fully billed and received; the clone must start fresh or
	# get_payment_entry and make_purchase_receipt both refuse it.
	po.per_billed = po.per_received = 0
	po.status = "Draft"
	# Terms are agreed at SOP steps 5-6. Clearing them here keeps the order-confirmation
	# gate under test regardless of which order this site hands back as the template.
	po.incoterm = po.named_place = po.payment_terms_template = None
	for row in po.items:
		row.schedule_date = frappe.utils.today()
		row.received_qty = row.billed_amt = row.returned_qty = 0
	po.custom_is_import = is_import
	if billing_address:
		po.billing_address = billing_address
		po.shipping_address = None
		po.shipping_address_display = None
	po.flags.ignore_permissions = True
	return po


# =====================================================================
print("=== Item hook: import items need a default supplier (Sahil 07-08) ===")
item_code = LINE.item_code
item = frappe.get_doc("Item", item_code)
had_flag = item.custom_is_import_item
had_defaults = [(d.company, d.default_supplier) for d in item.item_defaults]

item.custom_is_import_item = 1
for d in item.item_defaults:
	d.default_supplier = None

blocked("import item without a default supplier is refused",
        lambda: item.save(ignore_permissions=True), expect="Default Supplier")

if item.item_defaults:
	item.item_defaults[0].default_supplier = TEMPLATE.supplier
else:
	item.append("item_defaults", {"company": TEMPLATE.company,
	                              "default_supplier": TEMPLATE.supplier})
item.modified = frappe.db.get_value("Item", item_code, "modified")
try:
	item.save(ignore_permissions=True)
	check("import item saves once a default supplier is set", True)
except Exception as e:
	check("import item saves once a default supplier is set", False, str(e)[:120])

# =====================================================================
print("\n=== Purchase Order hook: import flag + consignee address (Sahil 07-08) ===")
po = make_po(billing_address="Supreme Equipments Pvt Ltd-Billing")
po.insert()
check("import item auto-flags the PO as an import", po.custom_is_import == 1,
      "custom_is_import=%s" % po.custom_is_import)
check("consignee (shipping) address matches the company billing address",
      po.shipping_address == "Supreme Equipments Pvt Ltd-Billing", po.shipping_address)
check("consignee address display populated", bool(po.shipping_address_display))

# a non-import item must not drag the flag on
item.custom_is_import_item = 0
item.modified = frappe.db.get_value("Item", item_code, "modified")
item.save(ignore_permissions=True)
po2 = make_po()
po2.insert()
check("non-import PO stays unflagged", not po2.custom_is_import,
      "custom_is_import=%s" % po2.custom_is_import)

# =====================================================================
print("\n=== Purchase Order hook: auto-create on submit ===")
po.reload()
po.custom_is_import = 1
po.save()
po.submit()
shipment_name = frappe.db.get_value("Import Shipment", {"purchase_order": po.name})
check("submitting an import PO creates the shipment", bool(shipment_name), shipment_name)

po2.reload()
po2.submit()
check("submitting a non-import PO creates nothing",
      not frappe.db.exists("Import Shipment", {"purchase_order": po2.name}))

ship = frappe.get_doc("Import Shipment", shipment_name)
check("shipment inherited the PO value", bool(ship.pi_value_confirmed), ship.pi_value_confirmed)

# =====================================================================
print("\n=== Payment Entry hook: advance recorded on the shipment (SOP 8) ===")
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

pe = get_payment_entry("Purchase Order", po.name)
pe.reference_no = "UTR-ADV-001"
pe.reference_date = frappe.utils.today()
pe.paid_amount = pe.paid_amount / 2
pe.received_amount = pe.received_amount / 2
for r in pe.references:
	r.allocated_amount = pe.paid_amount
pe.flags.ignore_permissions = True
pe.insert()
pe.submit()

ship.reload()
rows = [r for r in ship.payments if r.payment_entry == pe.name]
check("payment entry mirrored into the Payments table", len(rows) == 1, len(rows))
if rows:
	check("payment row is the advance leg", rows[0].payment_type == "Advance to Supplier",
	      rows[0].payment_type)
	check("UTR carried over from the payment entry", rows[0].utr_no == "UTR-ADV-001",
	      rows[0].utr_no)
	check("proof deliberately left for accounts to attach", not rows[0].payment_proof)

# Regression: advance_paid is denominated in the party account's currency, which on an
# import is the company's, not the order's. Comparing it against grand_total read a half
# payment on a USD order as settled in full.
from erpnext_import_tracker.events.payment_entry import is_fully_paid

paid = frappe.db.get_value("Purchase Order", po.name,
                           ["advance_paid", "grand_total", "base_grand_total", "currency",
                            "party_account_currency"], as_dict=True)
check("half-paid foreign-currency order is not read as fully paid",
      not is_fully_paid(paid),
      "advance_paid=%s %s vs grand_total=%s %s / base=%s"
      % (paid.advance_paid, paid.party_account_currency, paid.grand_total, paid.currency,
         paid.base_grand_total))
check("the same order reads as fully paid once it really is",
      is_fully_paid(frappe._dict(paid, advance_paid=paid.base_grand_total)))

# =====================================================================
print("\n=== Fetch Payments: catch up on entries the hook never mirrored ===")
from erpnext_import_tracker.import_tracker.doctype.import_shipment.import_shipment import (
	get_linked_payments,
)

ship.reload()
check("nothing to fetch while the hook's own row is on the shipment",
      get_linked_payments(ship.name) == [], get_linked_payments(ship.name))

# Stands in for the cases the hook cannot cover: a payment made before the shipment
# existed, or one whose mirror a stage gate rolled back. The Payment Entry is submitted,
# the Payments row is not there.
ship.payments = [r for r in ship.payments if r.payment_entry != pe.name]
ship.flags.ignore_permissions = True
ship.save()

fetched = get_linked_payments(ship.name)
check("fetch finds the unmirrored payment", len(fetched) == 1,
      [r["payment_entry"] for r in fetched])
if fetched:
	row = fetched[0]
	check("fetched row points at the payment entry", row["payment_entry"] == pe.name,
	      row["payment_entry"])
	check("fetched row is the advance leg while the order is part paid",
	      row["payment_type"] == "Advance to Supplier", row["payment_type"])
	check("UTR carried over", row["utr_no"] == "UTR-ADV-001", row["utr_no"])
	check("amount is the allocation, not the whole entry",
	      frappe.utils.flt(row["amount"]) == frappe.utils.flt(pe.paid_amount),
	      (row["amount"], pe.paid_amount))
	check("proof is left for accounts to attach", "payment_proof" not in row)

	ship.append("payments", row)
	ship.flags.ignore_permissions = True
	ship.save()
	check("a second fetch after saving adds nothing", get_linked_payments(ship.name) == [])

# Customs duty leaves the bank as a Journal Entry against the Bill of Entry, never a
# Payment Entry. The query path is the same for any journalled leg, so this exercises it
# against the order -- no India Compliance Bill of Entry needed on the test site.
from erpnext.accounts.party import get_party_account

company = frappe.get_cached_doc("Company", po.company)
je = frappe.new_doc("Journal Entry")
je.voucher_type = "Bank Entry"
je.company = po.company
je.posting_date = je.cheque_date = frappe.utils.today()
je.cheque_no = "UTR-JV-001"
je.append("accounts", {
	"account": get_party_account("Supplier", po.supplier, po.company),
	"party_type": "Supplier",
	"party": po.supplier,
	"debit_in_account_currency": 500,
	"reference_type": "Purchase Order",
	"reference_name": po.name,
	"cost_center": company.cost_center,
})
je.append("accounts", {
	"account": company.default_bank_account or company.default_cash_account,
	"credit_in_account_currency": 500,
	"cost_center": company.cost_center,
})
je.flags.ignore_permissions = True
je.insert()
je.submit()

journalled = [r for r in get_linked_payments(ship.name) if r.get("journal_entry")]
check("journal entry against the order is fetched", len(journalled) == 1, journalled)
if journalled:
	jrow = journalled[0]
	check("amount comes off the debit leg, not the credit",
	      frappe.utils.flt(jrow["amount"]) == 500.0, jrow["amount"])
	check("UTR comes off the journal's reference number", jrow["utr_no"] == "UTR-JV-001",
	      jrow["utr_no"])
	check("journal row carries the supplier", jrow["party"] == po.supplier, jrow["party"])

	ship.append("payments", jrow)
	ship.flags.ignore_permissions = True
	ship.save()
	check("the journal entry is not fetched twice",
	      not [r for r in get_linked_payments(ship.name) if r.get("journal_entry")])

# =====================================================================
print("\n=== workflow engine: transitions go through apply_workflow ===")
ship.reload()
blocked("workflow refuses to advance before the order is confirmed",
        lambda: apply_workflow(ship, "Mark Advance Paid"), expect="Confirm the order")

ship.reload()
ship.payment_terms_template = "30% Advance"
ship.advance_percentage = 30
ship.incoterm = "EXW"
for r in ship.payments:
	r.payment_proof = "/files/swift.pdf"
ship.flags.ignore_permissions = True
ship.save()

try:
	apply_workflow(ship, "Mark Advance Paid")
	check("apply_workflow advances to Advance Paid", ship.status == "Advance Paid", ship.status)
	check("stage_index followed the workflow action", ship.stage_index == 1, ship.stage_index)
except Exception as e:
	check("apply_workflow advances to Advance Paid", False, "%s: %s" % (type(e).__name__, str(e)[:120]))

actions = [t.action for t in frappe.get_doc("Workflow", "Import Shipment Process").transitions
           if t.state == "Advance Paid"]
check("both onward paths offered from Advance Paid",
      sorted(actions) == ["Mark Balance Paid", "Mark Docs Received"], actions)

# =====================================================================
print("\n=== Purchase Receipt hook: GRN must never be blocked by the tracker ===")
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

# Creating a fresh receipt on this site collides with a GRN naming counter left behind by
# the restored production data, so the handler is driven against a real submitted receipt.
# What matters is that a shipment which cannot satisfy its gates never propagates the
# failure back into the goods receipt.
from erpnext_import_tracker.events import purchase_receipt as pr_hook

existing_pr = frappe.db.sql("""
	SELECT pri.parent AS pr, pri.purchase_order AS po
	FROM `tabPurchase Receipt Item` pri JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
	WHERE pr.docstatus = 1 AND pri.purchase_order IS NOT NULL AND pri.purchase_order != ''
	  AND NOT EXISTS (SELECT 1 FROM `tabImport Shipment` s WHERE s.purchase_order = pri.purchase_order)
	ORDER BY pr.creation DESC LIMIT 1""", as_dict=True)

if not existing_pr:
	print("  (skipped: no submitted Purchase Receipt raised against a PO)")
else:
	row = existing_pr[0]
	frappe.db.set_value("Purchase Order", row.po, "custom_is_import", 1)
	early = frappe.get_doc("Import Shipment", create_shipment(row.po))
	check("shipment for the GRN scenario sits at Awaiting Shipment",
	      early.status == "Awaiting Shipment", early.status)

	pr_doc = frappe.get_doc("Purchase Receipt", row.pr)
	try:
		pr_hook.on_submit(pr_doc)
		check("GRN hook does not raise when the shipment cannot meet its gates", True)
	except Exception as e:
		check("GRN hook does not raise when the shipment cannot meet its gates", False,
		      "%s: %s" % (type(e).__name__, str(e)[:160]))

	early.reload()
	check("shipment was NOT force-advanced past its gates",
	      early.status == "Awaiting Shipment", early.status)
	check("no half-written state left behind by the refused advance",
	      not early.unloading_completed, "unloading_completed=%s" % early.unloading_completed)
	check("nor a stray GRN link", not early.purchase_receipt, early.purchase_receipt)

	# A shipment already closed at the bank must not be reopened by a later receipt.
	early.db_set("status", "Closed", update_modified=False)
	closed_before = frappe.db.get_value("Import Shipment", early.name, "modified")
	pr_hook.on_submit(pr_doc)
	check("a Closed shipment is left alone",
	      frappe.db.get_value("Import Shipment", early.name, "modified") == closed_before)

frappe.db.rollback()

# restore the item flag we toggled (rollback covers it, but be explicit)
frappe.db.rollback()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
	for f in FAIL:
		print("  FAILED: %s" % f)
	raise SystemExit(1)
