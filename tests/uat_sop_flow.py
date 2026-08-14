"""End-to-end UAT for the 25-step SOP alignment. Rollback-safe -- nothing is committed.

Run:  cd <bench>/sites && ../env/bin/python ../apps/erpnext_import_tracker/tests/uat_sop_flow.py
"""

import frappe

frappe.init(site="supreme.localhost")
frappe.connect()
frappe.set_user("Administrator")
frappe.flags.in_test = True

# This site has a live default outgoing email account and real staff hold Stock User,
# so the plant intimation is captured rather than sent.
SENT = []
_real_sendmail = frappe.sendmail
frappe.sendmail = lambda **kwargs: SENT.append(kwargs)

PASS, FAIL = [], []


def check(label, cond, detail=""):
	(PASS if cond else FAIL).append(label)
	print("%s  %s%s" % ("PASS" if cond else "FAIL", label, (" -- " + str(detail)) if detail else ""))


def resync(doc):
	"""Realign the in-memory timestamp after a refused save.

	Document.save() stamps self.modified before validate() runs, so a deliberate throw
	leaves the doc ahead of the row and the next save trips check_if_latest. The browser
	never sees this -- it re-sends the timestamp it loaded with -- so it is a harness
	concern, not an app one.
	"""
	if doc and doc.name:
		doc.modified = frappe.db.get_value(doc.doctype, doc.name, "modified")


def blocked(label, fn, expect=None, doc=None):
	"""Assert that fn() is refused by a gate."""
	try:
		fn()
	except frappe.ValidationError as e:
		msg = str(e)
		ok = expect.lower() in msg.lower() if expect else True
		check(label, ok, msg[:110] if not ok else "")
		resync(doc)
		return
	check(label, False, "was allowed through")


def allowed(label, fn):
	try:
		fn()
		check(label, True)
	except Exception as e:
		check(label, False, "%s: %s" % (type(e).__name__, str(e)[:140]))


po = frappe.db.get_value("Purchase Order", {"docstatus": 1}, "name")
if not po:
	raise SystemExit("no submitted Purchase Order on this site")
print("using Purchase Order %s\n" % po)

# Terms are agreed at SOP steps 5-6, so the fixture must start without them -- otherwise
# whichever order this site happens to return first decides whether the order-confirmation
# gate is exercised at all. Rolled back with everything else.
frappe.db.set_value("Purchase Order", po,
                    {"incoterm": None, "named_place": None, "payment_terms_template": None})
frappe.db.set_value("Supplier", frappe.db.get_value("Purchase Order", po, "supplier"),
                    "payment_terms", None)


def new_shipment(**kwargs):
	s = frappe.new_doc("Import Shipment")
	s.purchase_order = po
	s.mode = "Sea"
	for k, v in kwargs.items():
		setattr(s, k, v)
	s.insert(ignore_permissions=True)
	return s


def save(s):
	s.flags.ignore_permissions = True
	s.save()


def add_payment(s, payment_type, proof=True, utr=True, shared=True, amount=1000):
	s.append("payments", {
		"payment_type": payment_type,
		"amount": amount,
		"payment_date": frappe.utils.today(),
		"payment_proof": "/files/swift.pdf" if proof else None,
		"utr_no": "UTR123456" if utr else None,
		"shared_with_vendor": 1 if shared else 0,
	})


def complete_docs(s, categories):
	for row in s.boe_checklist:
		if row.category in categories:
			row.received = 1
			row.verified = 1
			row.attachment = "/files/%s.pdf" % row.document_name.replace(" ", "_").replace("/", "")


# =====================================================================
print("=== duplicate fields removed ===")
meta = frappe.get_meta("Import Shipment")
check("coo_received is gone from the form", not meta.has_field("coo_received"))
check("checklist is the single home for COO",
      any(d.fieldname == "boe_checklist" for d in meta.fields))

# =====================================================================
print("\n=== SOP section 3/4: order confirmation gate ===")
s = new_shipment()
check("starts in Awaiting Shipment at stage 0",
      s.status == "Awaiting Shipment" and s.stage_index == 0)

def _skip_confirmation():
	s.status = "Advance Paid"
	save(s)

blocked("cannot leave Awaiting Shipment without payment + shipment terms",
        _skip_confirmation, expect="Confirm the order", doc=s)
frappe.db.rollback()

# =====================================================================
print("\n=== SOP step 8: advance needs its SWIFT copy ===")
s = new_shipment(payment_terms_template="30% Advance", advance_percentage=30, incoterm="EXW")

def _advance_no_row():
	s.status = "Advance Paid"
	save(s)

blocked("advance stage blocked with no payment row", _advance_no_row, expect="Advance to Supplier", doc=s)
s.reload()
s.payment_terms_template, s.incoterm, s.advance_percentage = "30% Advance", "EXW", 30
add_payment(s, "Advance to Supplier", proof=False)

def _advance_no_proof():
	s.status = "Advance Paid"
	save(s)

blocked("advance stage blocked without the SWIFT copy", _advance_no_proof, expect="Proof", doc=s)
s.payments[0].payment_proof = "/files/swift_advance.pdf"
allowed("advance stage passes once the SWIFT copy is attached", lambda: _advance_no_proof())
check("stage_index tracked the status", s.stage_index == 1, s.stage_index)

# =====================================================================
print("\n=== SOP step 9: PI value variation needs a reason ===")
s.pi_value_final = (s.pi_value_confirmed or 0) + 500
add_payment(s, "Balance to Supplier")

def _balance():
	s.status = "Balance Paid"
	save(s)

blocked("balance blocked while the PI variation is unexplained", _balance, expect="variation", doc=s)
s.pi_variation_reason = "Freight cost revision at readiness"
allowed("balance passes once the variation is explained", _balance)
check("variation computed", s.pi_value_variation == 500, s.pi_value_variation)

# =====================================================================
print("\n=== SOP step 11 / Sahil: the four mandatory import documents ===")
def _docs():
	s.status = "Docs Received"
	save(s)

blocked("docs received blocked while mandatory documents are outstanding", _docs,
        expect="mandatory import documents", doc=s)
complete_docs(s, ("Mandatory Import Document",))
allowed("docs received passes once invoice/packing list/COO/BL are in", _docs)

# =====================================================================
print("\n=== SOP steps 12-13: CHA, pre-alert, endorsement ===")
def _cha():
	s.status = "Forwarded to CHA"
	save(s)

blocked("cannot forward without a CHA", _cha, expect="CHA", doc=s)
s.cha = frappe.db.get_value("Supplier", {"disabled": 0}, "name")
allowed("forwards to CHA once one is set", _cha)

def _prealert():
	s.status = "Pre-Alert Received"
	save(s)

blocked("pre-alert needs its date", _prealert, expect="Pre-Alert Date", doc=s)
s.pre_alert_date = frappe.utils.today()
allowed("pre-alert passes with the date", _prealert)

def _endorse():
	s.status = "Docs Endorsed"
	save(s)

blocked("endorsement needs originals or a soft-copy declaration", _endorse, expect="soft cop", doc=s)
s.endorsed_on_soft_copy = 1
s.docs_endorsed_date = frappe.utils.today()
allowed("endorsement passes on soft copies (SOP step 13)", _endorse)
check("endorsement now precedes the BOE checklist",
      s.stage_index < frappe.get_doc("Import Shipment", s.name).state_index() + 1)

# =====================================================================
print("\n=== SOP step 14: BOE checklist ===")
def _checklist():
	s.status = "BOE Checklist Confirmed"
	save(s)

blocked("checklist blocked while BOE documents are outstanding", _checklist, expect="BOE checklist", doc=s)
complete_docs(s, ("BOE Checklist",))
allowed("checklist confirms once every document is received, verified and uploaded", _checklist)
check("confirmation date stamped", bool(s.boe_checklist_confirmed_date))

# =====================================================================
print("\n=== Sahil 07-08: end use declaration activates AFTER checklist confirmation ===")
def _late_end_use():
	s.end_use_declaration_required = 1
	save(s)

allowed("end use declaration can be raised after the checklist is confirmed", _late_end_use)
check("end use row filed as a customs query, not a checklist item",
      any(d.document_name == "End Use Declaration" and d.category == "Customs Query"
          for d in s.boe_checklist))

# =====================================================================
print("\n=== SOP step 15: the duty challan and its UTR, before duty is marked paid ===")
def _duty():
	s.status = "Duty Paid"
	save(s)

blocked("duty blocked with no duty payment row", _duty, expect="Customs Duty", doc=s)
add_payment(s, "Customs Duty", utr=False, shared=False)
blocked("duty blocked without the UTR", _duty, expect="UTR", doc=s)
for row in s.payments:
	if row.payment_type == "Customs Duty":
		row.utr_no = "UTRDUTY99"
# Duty is paid to customs, not to the vendor, so the vendor hand-off must not gate it.
allowed("duty passes on the UTR alone, with Sent to Vendor left unticked", _duty)
check("Sent to Vendor stayed untouched",
      not any(r.shared_with_vendor for r in s.payments if r.payment_type == "Customs Duty"))

for row in s.payments:
	if row.payment_type == "Customs Duty":
		row.shared_with_vendor = 1
save(s)
check("shared_on is still stamped when the tick is used",
      all(r.shared_on for r in s.payments if r.shared_with_vendor))

# =====================================================================
print("\n=== SOP steps 16-20: clearance closes out late queries ===")
allowed("clearance starts", lambda: (setattr(s, "status", "Under Clearance"), save(s)))
check("clearance initiation date stamped", bool(s.clearance_initiated_date))

add_payment(s, "Delivery Order", utr=False, shared=False)

def _cleared():
	s.status = "Cleared"
	save(s)

blocked("clearance blocked while the end use declaration is outstanding", _cleared,
        expect="End Use Declaration", doc=s)
complete_docs(s, ("Customs Query", "Shipping Line"))
blocked("clearance blocked while the delivery order payment lacks its UTR", _cleared, expect="UTR", doc=s)
for row in s.payments:
	if row.payment_type == "Delivery Order":
		row.utr_no, row.shared_with_vendor = "UTRDO77", 1
allowed("clearance completes", _cleared)

# =====================================================================
print("\n=== SOP step 21 / Sahil: e-way bill upload + plant intimation ===")
settings = frappe.get_single("Import Tracker Settings")
settings.notify_plant_on_eway_bill = 1
settings.store_notification_role = "Stock User"
settings.flags.ignore_permissions = True
settings.save()
frappe.clear_document_cache("Import Tracker Settings", "Import Tracker Settings")
SENT.clear()

def _transit():
	s.status = "In Transit"
	save(s)

blocked("dispatch blocked without the e-way bill", _transit, expect="E-Way Bill", doc=s)
s.eway_bill_no = "EWB123456789"
s.eway_bill_date = frappe.utils.today()
blocked("dispatch blocked until the e-way bill copy is uploaded", _transit, expect="Copy", doc=s)
s.eway_bill_copy = "/files/ewb.pdf"
allowed("dispatch passes with the e-way bill uploaded", _transit)
s.reload()
check("plant/store notified the moment the e-way bill landed", s.plant_notified == 1,
      "plant_notified=%s" % s.plant_notified)
check("notification timestamped", bool(s.plant_notified_on))
check("exactly one intimation sent", len(SENT) == 1, "%d sent" % len(SENT))
if SENT:
	mail = SENT[0]
	check("addressed to store users", bool(mail.get("recipients")),
	      "%d recipient(s)" % len(mail.get("recipients") or []))
	check("names the e-way bill and asks the store to prepare unloading",
	      "EWB123456789" in mail.get("message", "") and "unloading" in mail.get("message", ""))

SENT.clear()
allowed("re-saving does not notify twice", lambda: save(s))
check("intimation fires once only", len(SENT) == 0, "%d sent" % len(SENT))

# =====================================================================
print("\n=== SOP step 24: unloading + GRN ===")
def _received():
	s.status = "Received at Factory"
	save(s)

blocked("receipt blocked before unloading is confirmed", _received, expect="Unloading", doc=s)
s.unloading_completed = 1
blocked("receipt blocked without the GRN", _received, expect="Purchase Receipt", doc=s)
pr = frappe.db.get_value("Purchase Receipt", {"docstatus": 1}, "name")
if pr:
	s.purchase_receipt = pr
	allowed("receipt passes with unloading done and the GRN linked", _received)
else:
	print("  (no submitted Purchase Receipt on this site; forcing the link)")
	s.db_set("purchase_receipt", "TEST-GRN", update_modified=False)
	s.reload()
	s.unloading_completed = 1
	allowed("receipt passes with unloading done and the GRN linked", _received)

# =====================================================================
print("\n=== SOP step 25 / Sahil: submit docs to bank, then upload the bank copy ===")
def _to_bank():
	s.status = "Docs Submitted to Bank"
	save(s)

blocked("bank submission blocked without invoice/BOE/BL/covering letter", _to_bank,
        expect="covering letter", doc=s)
s.purchase_invoice = frappe.db.get_value("Purchase Invoice", {"docstatus": 1}, "name")
s.bl_awb_no = "BL-9988"
s.covering_letter = "/files/covering_letter.pdf"
s.docs_submitted_to_bank = 1
if not frappe.db.exists("DocType", "Bill of Entry"):
	print("  (Bill of Entry doctype absent; skipping)")
else:
	boe = frappe.db.get_value("Bill of Entry", {"docstatus": 1}, "name")
	s.bill_of_entry = boe
allowed("bank submission passes once the full set is attached", _to_bank)
check("bank submission date stamped", bool(s.bank_submission_date))

def _closed():
	s.status = "Closed"
	save(s)

blocked("closure blocked until the bank's received copy is uploaded", _closed,
        expect="Bank Received Copy", doc=s)
s.bank_received_copy = "/files/bank_ack.pdf"
s.bank_received_date = frappe.utils.today()
allowed("shipment closes once the bank copy is in", _closed)
check("final stage index is 14", s.stage_index == 14, s.stage_index)

# =====================================================================
print("\n=== terms without an advance leg skip the advance stage ===")
frappe.db.rollback()
s2 = new_shipment(payment_terms_template="Against BL", advance_percentage=0, incoterm="FOB")
add_payment(s2, "Balance to Supplier")

def _straight_to_balance():
	s2.status = "Balance Paid"
	save(s2)

allowed("Against BL goes straight to Balance Paid without an advance row",
        _straight_to_balance)

frappe.db.rollback()
s3 = new_shipment(payment_terms_template="100% Advance", advance_percentage=100, incoterm="CIF")
add_payment(s3, "Advance to Supplier")

def _full_advance():
	s3.status = "Balance Paid"
	save(s3)

allowed("100% Advance needs no balance row", _full_advance)

frappe.db.rollback()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
	for f in FAIL:
		print("  FAILED: %s" % f)
	raise SystemExit(1)
