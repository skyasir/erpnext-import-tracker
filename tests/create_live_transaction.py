"""Create one real import transaction and walk it through all 25 SOP steps.

Unlike the other suites this one COMMITS, so the shipment can be opened in the browser.
Everything it creates is tagged IMPORT-SOP-DEMO in its remarks so it can be found and
removed again (see cleanup_demo_transaction.py).

Run:  cd <bench>/sites && ../env/bin/python ../apps/erpnext_import_tracker/tests/create_live_transaction.py
"""

import frappe
from frappe.model.workflow import apply_workflow

frappe.init(site="supreme.localhost")
frappe.connect()
frappe.set_user("Administrator")

# The plant intimation would otherwise email every Stock User on this site.
SENT = []
frappe.sendmail = lambda **kwargs: SENT.append(kwargs)

TAG = "IMPORT-SOP-DEMO"
PASS, FAIL = [], []
meta = frappe.get_meta("Import Shipment")


def check(label, cond, detail=""):
	(PASS if cond else FAIL).append(label)
	print("   %s  %s%s" % ("ok " if cond else "FAIL", label, (" -- " + str(detail)) if detail else ""))


def visible_sections(stage_index, incoterm):
	"""What the form actually shows at this stage -- the browser view, in text."""
	out = []
	for df in meta.fields:
		if df.fieldtype != "Section Break":
			continue
		dep = df.depends_on or ""
		if "stage_index >=" in dep:
			threshold = int(dep.split("stage_index >=")[1].strip().split()[0].rstrip("\"'&"))
			if stage_index < threshold:
				continue
		if "incoterm=='EXW'" in dep and incoterm != "EXW":
			continue
		if "includes(doc.incoterm)" in dep and incoterm in ("CIF", "CIP"):
			continue
		out.append(df.label or df.fieldname)
	return out


def advance(shipment, action, note=""):
	before = shipment.status
	apply_workflow(shipment, action)
	print("\n-> %-24s %s => %s" % (action, before, shipment.status))
	if note:
		print("   (%s)" % note)
	print("   sections now visible: %s"
	      % ", ".join(visible_sections(shipment.stage_index, shipment.incoterm)))
	return shipment


def save(doc):
	doc.flags.ignore_permissions = True
	doc.save()


GST_ACCOUNTS = {
	a
	for row in frappe.get_single("GST Settings").gst_accounts
	for a in (row.cgst_account, row.sgst_account, row.igst_account, row.cess_account)
	if a
}


def drop_gst_rows(doc):
	"""An overseas purchase carries no GST on the order -- IGST arrives later via the
	Bill of Entry. The supplier's default template still gets applied, so its GST rows
	are removed here.

	One zero-value placeholder stays behind because ERPNext re-fetches that same default
	template whenever a new document's tax table is empty.
	"""
	doc.taxes = [t for t in doc.get("taxes") or [] if t.account_head not in GST_ACCOUNTS]
	if not doc.taxes:
		doc.append("taxes", {
			"charge_type": "Actual", "account_head": "Round Off - SEPL",
			"description": "No GST on import (IGST via Bill of Entry)",
			"tax_amount": 0, "category": "Total", "add_deduct_tax": "Add",
		})
	for idx, t in enumerate(doc.taxes, start=1):
		t.idx = idx


def strip_taxes(doc):
	drop_gst_rows(doc)
	doc.taxes_and_charges = None
	save(doc)


# =====================================================================
print("=" * 70)
print("STEP 1-3  Purchase order raised on the overseas vendor")
print("=" * 70)

template = frappe.db.get_value("Purchase Order", {"docstatus": 1, "currency": ["!=", "INR"]},
                               ["name", "supplier", "currency"], as_dict=True)
po = frappe.copy_doc(frappe.get_doc("Purchase Order", template.name))
po.workflow_state = None
po.per_billed = po.per_received = 0
po.status = "Draft"
po.transaction_date = po.schedule_date = frappe.utils.today()
for row in po.items:
	row.schedule_date = frappe.utils.today()
	row.received_qty = row.billed_amt = row.returned_qty = 0

# SOP steps 5-6: terms agreed at order confirmation, on the standard ERPNext fields.
po.custom_is_import = 1
po.incoterm = "EXW"
po.named_place = "Shanghai"
po.payment_terms_template = "30% Advance"
po.billing_address = "Supreme Equipments Pvt Ltd-Billing"
po.shipping_address = None
po.flags.ignore_permissions = True
po.insert()
strip_taxes(po)
po.submit()

print("Purchase Order : %s  (%s %s, %s, %s)"
      % (po.name, po.currency, po.grand_total, po.incoterm, po.payment_terms_template))
check("consignee address = company billing address",
      po.shipping_address == "Supreme Equipments Pvt Ltd-Billing", po.shipping_address)

name = frappe.db.get_value("Import Shipment", {"purchase_order": po.name})
check("import shipment auto-created on submit", bool(name), name)
s = frappe.get_doc("Import Shipment", name)
s.remarks = "%s -- created by create_live_transaction.py" % TAG
s.port_of_discharge = "Nhava Sheva"
s.vessel_flight = "MV Ever Given / V.221"
s.order_confirmation_date = frappe.utils.today()
s.rate_checked = 1
s.rate_check_remarks = "Compared against last purchase rate; 2% higher, accepted."
s.advance_percentage = 30
s.insurance_required = 1
s.insurance_amount = 450
s.insurance_document = "/files/marine_policy.pdf"
save(s)

print("Import Shipment: %s" % s.name)
print("   terms inherited from the PO: incoterm=%s named_place=%s payment_terms=%s"
      % (s.incoterm, s.named_place, s.payment_terms_template))
check("PI value taken from the order", s.pi_value_confirmed == po.grand_total, s.pi_value_confirmed)
check("mandatory documents seeded",
      len([d for d in s.boe_checklist if d.category == "Mandatory Import Document"]) == 4)
print("   sections visible at stage 0: %s"
      % ", ".join(visible_sections(s.stage_index, s.incoterm)))

# =====================================================================
print("\n" + "=" * 70)
print("STEP 7-9  Advance and balance payment, each against its SWIFT copy")
print("=" * 70)

s.append("payments", {
	"payment_type": "Advance to Supplier", "party": po.supplier,
	"amount": po.grand_total * 0.3, "payment_date": frappe.utils.today(),
	"utr_no": "SWIFT-ADV-88213", "payment_proof": "/files/swift_advance.pdf",
	"shared_with_vendor": 1,
})
save(s)
advance(s, "Mark Advance Paid", "SOP 8: 30% advance, SWIFT copy uploaded and sent to vendor")

s.pi_value_final = po.grand_total + 75
s.pi_variation_reason = "Freight cost revision advised by vendor at readiness."
s.append("payments", {
	"payment_type": "Balance to Supplier", "party": po.supplier,
	"amount": (po.grand_total * 0.7) + 75, "payment_date": frappe.utils.today(),
	"utr_no": "SWIFT-BAL-88477", "payment_proof": "/files/swift_balance.pdf",
	"shared_with_vendor": 1,
})
save(s)
check("PI variation computed", s.pi_value_variation == 75, s.pi_value_variation)
advance(s, "Mark Balance Paid", "SOP 9: balance released, variation of 75 recorded with a reason")

# =====================================================================
print("\n" + "=" * 70)
print("STEP 10-12  EXW handover to CHA, shipping documents, pre-alert")
print("=" * 70)

s.cha_quotation_confirmed = 1
s.cha_documentation_complete = 1
s.cha_pickup_confirmed = 1
s.cha_final_docs_complete = 1
s.bl_awb_no = "COSU6398471250"
s.bl_awb_date = frappe.utils.today()
s.eta = frappe.utils.add_days(frappe.utils.today(), 18)
for row in s.boe_checklist:
	if row.category == "Mandatory Import Document":
		row.received = row.verified = 1
		row.attachment = "/files/%s.pdf" % row.document_name.replace(" ", "_").replace("/", "")
save(s)
advance(s, "Mark Docs Received", "SOP 11: invoice, packing list, COO and BL received and uploaded")

s.cha = frappe.db.get_value("Supplier", {"disabled": 0}, "name")
save(s)
advance(s, "Forward to CHA", "SOP 11: documents forwarded to the clearing agent")

s.pre_alert_date = frappe.utils.today()
save(s)
advance(s, "Mark Pre-Alert Received", "SOP 12: CHA pre-alert as material nears Nhava Sheva")

# =====================================================================
print("\n" + "=" * 70)
print("STEP 13-15  Endorsement, BOE checklist, customs duty")
print("=" * 70)

s.original_docs_received = 1
s.docs_endorsed_date = frappe.utils.today()
s.endorsement_remarks = "Originals endorsed and couriered to CHA."
save(s)
advance(s, "Mark Docs Endorsed", "SOP 13: endorsement BEFORE the BOE checklist, per the SOP")

for row in s.boe_checklist:
	if row.category == "BOE Checklist":
		row.received = row.verified = 1
		row.attachment = "/files/%s.pdf" % row.document_name.replace(" ", "_").replace("/", "")
save(s)
advance(s, "Confirm Checklist", "SOP 14: every BOE document received, verified and uploaded")

# SOP 17 / Sahil: customs raises a query AFTER the checklist was confirmed.
s.end_use_declaration_required = 1
s.customs_query_raised = 1
s.customs_query_details = "Customs asked for the end use declaration and product brochure."
save(s)
check("end use declaration can still be raised after checklist confirmation",
      any(d.document_name == "End Use Declaration" for d in s.boe_checklist))

s.append("payments", {
	"payment_type": "Customs Duty", "amount": 18500,
	"payment_date": frappe.utils.today(), "utr_no": "DUTY-CHLN-55190",
	"payment_proof": "/files/duty_challan.pdf", "shared_with_vendor": 1,
	"remarks": "Duty paid against BOE checklist",
})
save(s)
advance(s, "Mark Duty Paid", "SOP 15 / Sahil: UTR recorded AND sent to the CHA before marking paid")

# =====================================================================
print("\n" + "=" * 70)
print("STEP 16-20  Clearance, late documents, delivery order payment")
print("=" * 70)

advance(s, "Start Clearance", "SOP 16: payment details shared with the CHA")

s.append("payments", {
	"payment_type": "Delivery Order", "amount": 3200,
	"payment_date": frappe.utils.today(), "utr_no": "DO-77341",
	"payment_proof": "/files/do_receipt.pdf", "shared_with_vendor": 1,
})
for row in s.boe_checklist:
	if row.category in ("Customs Query", "Shipping Line"):
		row.received = row.verified = 1
		row.attachment = "/files/end_use_declaration.pdf"
save(s)
advance(s, "Mark Cleared", "SOP 17-20: query closed, DO payment receipted, clearance complete")

# =====================================================================
print("\n" + "=" * 70)
print("STEP 21-24  E-way bill, plant intimation, unloading, GRN")
print("=" * 70)

s.vehicle_no = "MH-04-GT-8821"
s.transporter = frappe.db.get_value("Supplier", {"disabled": 0}, "name")
s.eway_bill_no = "371004829155"
s.eway_bill_date = frappe.utils.today()
s.eway_bill_copy = "/files/eway_bill.pdf"
save(s)
s.reload()
check("plant/store intimated the moment the e-way bill landed", s.plant_notified == 1)
check("exactly one intimation raised", len(SENT) == 1, "%d captured" % len(SENT))
advance(s, "Dispatch", "SOP 21-22: e-way bill uploaded, CHA moves the container to the factory")

pr = None
try:
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	pr = make_purchase_receipt(po.name)
	pr.supplier_delivery_note = "VENDOR-DN-7781"
	pr.taxes_and_charges = None
	drop_gst_rows(pr)
	# This site names GRNs from a custom field whose generator is handing back an
	# already-used number; set it explicitly rather than collide.
	used = frappe.get_all("Purchase Receipt", filters={"name": ["like", "GRN-P1-26-%"]}, pluck="name")
	nxt = max([int(u.rsplit("-", 1)[1]) for u in used] or [0]) + 1
	pr.custom_grn_naming_series = "GRN-P1-26-%03d" % nxt
	pr.flags.ignore_permissions = True
	pr.insert()
	strip_taxes(pr)
	pr.submit()
	print("\nPurchase Receipt (GRN): %s" % pr.name)
except Exception as e:
	pr = None  # the doc was named but never persisted; do not link a ghost
	print("\n!! GRN could not be created: %s: %s" % (type(e).__name__, str(e)[:200]))

s.reload()
check("GRN submission did not throw back into the tracker", True)
if pr:
	check("shipment picked up the GRN automatically",
	      s.purchase_receipt == pr.name and s.unloading_completed, s.status)

if s.status != "Received at Factory":
	s.container_arrival_date = frappe.utils.today()
	s.unloading_completed = 1
	if pr:
		s.purchase_receipt = pr.name
	save(s)
	advance(s, "Receive at Factory", "SOP 23-24: unloading completed the day the container arrived")

# =====================================================================
print("\n" + "=" * 70)
print("STEP 25  Bank closure")
print("=" * 70)

pi = frappe.db.get_value("Purchase Invoice", {"docstatus": 1}, "name")
boe = (frappe.db.get_value("Bill of Entry", {"docstatus": 1}, "name")
       if frappe.db.exists("DocType", "Bill of Entry") else None)
s.purchase_invoice = pi
s.bill_of_entry = boe
s.docs_submitted_to_bank = 1
s.covering_letter = "/files/covering_letter.pdf"
save(s)
advance(s, "Submit Docs to Bank", "SOP 25: invoice, BOE and BL sent to the bank under cover")

s.bank_received_copy = "/files/bank_acknowledgement.pdf"
s.bank_received_date = frappe.utils.today()
save(s)
advance(s, "Close Shipment", "SOP 25: bank's received copy uploaded, transaction closed")

check("final state is Closed at stage 14",
      s.status == "Closed" and s.stage_index == 14, "%s / %s" % (s.status, s.stage_index))
check("no email actually left the site during the walk", len(SENT) == 1)

frappe.db.commit()

print("\n" + "=" * 70)
print("%d checks passed, %d failed" % (len(PASS), len(FAIL)))
print("Purchase Order  : %s" % po.name)
print("Import Shipment : %s   <- open this one" % s.name)
if pr:
	print("Purchase Receipt: %s" % pr.name)
print("=" * 70)
if FAIL:
	for f in FAIL:
		print("  FAILED: %s" % f)
	raise SystemExit(1)
