import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form, now_datetime, today

from erpnext_import_tracker.install import STATES

# Single source of truth for the workflow order, shared with install.py so the form
# layout, the gates below and the workflow itself cannot drift apart.
STATE_ORDER = [state for state, _role, _style in STATES]

# SOP section 5 step 11 / Sahil 07-08: every import carries these four, whatever else
# the shipment needs. Seeded on insert and enforced before "Docs Received".
MANDATORY_DOCUMENTS = [
	"Commercial Invoice",
	"Packing List",
	"Certificate of Origin",
	"BL / AWB",
]

# Seeded alongside the mandatory four; needed for the BOE filing but not for goods to move.
BOE_CHECKLIST_DOCUMENTS = [
	"Insurance",
	"Purchase Order Copy",
]

# Documents that customs or the shipping line may ask for at any stage before GRN
# (SOP steps 17-18). They are deliberately excluded from the BOE checklist gate so
# ticking "End Use Declaration Required" after the checklist is confirmed does not
# retroactively invalidate the shipment.
LATE_DOCUMENT_CATEGORIES = ("Customs Query", "Shipping Line")

# SOP section 3: whether an order has an advance leg is read off the agreed advance
# percentage, not off the name of the terms -- "Against BL", "Against Readiness" and
# "Prior To Dispatch" all settle in one go later and carry 0%.
FULL_ADVANCE = 100


class ImportShipment(Document):
	# ------------------------------------------------------------------
	# lifecycle
	# ------------------------------------------------------------------
	def before_insert(self):
		self.set_defaults_from_order()
		if not self.boe_checklist:
			self.seed_document_checklist()

	def onload(self):
		"""Recompute the stage index when the form is opened.

		Section visibility keys off stage_index, so a stored value that has drifted from
		status -- a direct db_set, a restored row, an older app version -- would hide the
		very section a gate is asking the user to fill, with no way out. Display only;
		validate() persists the correct value on the next save.
		"""
		current = self.state_index()
		if self.stage_index != current:
			self.stage_index = current

	def validate(self):
		self.set_defaults_from_order()
		self.sync_end_use_row()
		self.compute_pi_variation()
		self.stamp_payment_sharing()
		self.stage_index = self.state_index()
		self.stamp_stage_dates()
		self.validate_state_requirements()

	def on_update(self):
		self.notify_plant_of_dispatch()

	# ------------------------------------------------------------------
	# defaults
	# ------------------------------------------------------------------
	def set_defaults_from_order(self):
		"""Fill what the field-level fetches cannot.

		Incoterm, Named Place and Payment Terms Template are standard ERPNext fields the
		buyer already agreed on the order (SOP steps 5-6); they arrive here through
		fetch_from, editable because fetch_if_empty is set. Left to do is the PI value,
		and the supplier's default template for orders raised before terms were recorded.
		"""
		if not self.purchase_order:
			return

		po = frappe.db.get_value(
			"Purchase Order", self.purchase_order, ["grand_total", "supplier"], as_dict=True
		)
		if not po:
			return

		if not self.pi_value_confirmed:
			self.pi_value_confirmed = po.grand_total
		if not self.payment_terms_template and po.supplier:
			self.payment_terms_template = frappe.db.get_value(
				"Supplier", po.supplier, "payment_terms"
			)

	def seed_document_checklist(self):
		for name in MANDATORY_DOCUMENTS:
			self.append(
				"boe_checklist",
				{"category": "Mandatory Import Document", "document_name": name, "is_mandatory": 1},
			)
		for name in BOE_CHECKLIST_DOCUMENTS:
			self.append(
				"boe_checklist",
				{"category": "BOE Checklist", "document_name": name, "is_mandatory": 1},
			)

	def sync_end_use_row(self):
		"""Add or remove the conditional End Use Declaration row (SOP step 17).

		Filed under Customs Query so it is checked at clearance rather than at BOE
		checklist confirmation -- customs can ask for it at any stage before GRN.
		"""
		row = next(
			(d for d in self.boe_checklist if d.document_name == "End Use Declaration"), None
		)
		if self.end_use_declaration_required and not row:
			self.append(
				"boe_checklist",
				{
					"category": "Customs Query",
					"document_name": "End Use Declaration",
					"is_mandatory": 1,
				},
			)
		elif not self.end_use_declaration_required and row:
			if row.received or row.verified or row.attachment:
				frappe.throw(
					_("The End Use Declaration has already been actioned and cannot be "
					  "withdrawn. Clear the row's Received / Verified / Attachment first.")
				)
			self.boe_checklist.remove(row)
			for idx, d in enumerate(self.boe_checklist, start=1):
				d.idx = idx

	def compute_pi_variation(self):
		"""SOP step 9: the PI value confirmed at order time may move at balance payment
		because of production or freight cost changes. Record the delta, not a retyped total."""
		if self.pi_value_final:
			self.pi_value_variation = flt(self.pi_value_final) - flt(self.pi_value_confirmed)
		else:
			self.pi_value_variation = 0

	def stamp_payment_sharing(self):
		for row in self.payments:
			if row.shared_with_vendor and not row.shared_on:
				row.shared_on = today()
			elif not row.shared_with_vendor:
				row.shared_on = None

	def stamp_stage_dates(self):
		"""Dates that only record "when did we do this" default to today the first time
		the shipment reaches that stage. Still editable afterwards."""
		stamps = (
			("BOE Checklist Confirmed", "boe_checklist_confirmed_date"),
			("Duty Paid", "duty_paid_date"),
			("Under Clearance", "clearance_initiated_date"),
			("Cleared", "clearance_date"),
			("Received at Factory", "received_date"),
			("Docs Submitted to Bank", "bank_submission_date"),
		)
		for state, fieldname in stamps:
			if self.reached(state) and not self.get(fieldname):
				self.set(fieldname, today())

	# ------------------------------------------------------------------
	# stage helpers
	# ------------------------------------------------------------------
	def state_index(self):
		try:
			return STATE_ORDER.index(self.status)
		except ValueError:
			return 0

	def reached(self, state):
		return self.state_index() >= STATE_ORDER.index(state)

	def payment_rows(self, payment_type):
		return [row for row in self.payments if row.payment_type == payment_type]

	def require_payment(self, payment_type, label, need_utr=False):
		"""SOP: a payment stage completes only once its proof is uploaded.

		need_utr additionally demands the transaction reference, on the legs where someone
		downstream has to quote it -- duty, CHA and delivery-order payments.

		"Sent to Vendor" is deliberately NOT gated here. These are exactly the legs paid to
		customs, the CHA or the shipping line rather than to the vendor, so demanding the
		vendor hand-off before the stage can close blocks work that is genuinely finished.
		The tick stays on the row for whoever does need to record it.
		"""
		rows = self.payment_rows(payment_type)
		if not rows:
			frappe.throw(
				_("Add a {0} row in the Payments table before moving to this stage.").format(label)
			)

		for row in rows:
			missing = [
				text
				for text, value in (
					(_("Amount"), row.amount),
					(_("Paid On"), row.payment_date),
					(_("Proof (SWIFT copy / receipt)"), row.payment_proof),
				)
				if not value
			]
			if need_utr and not row.utr_no:
				missing.append(_("UTR / Transaction No"))
			if missing:
				frappe.throw(
					_("Payments row {0} ({1}) is incomplete. Missing: {2}").format(
						row.idx, label, ", ".join(missing)
					)
				)

	def pending_documents(self, categories=None, mandatory_only=True):
		"""Documents still short of received + verified + attached, each labelled with what
		is actually missing.

		Naming the gap matters: a row with both ticks on and no file looks complete in the
		grid, so a bare document name reads as a false alarm and sends people hunting.
		"""
		rows = self.boe_checklist
		if categories:
			rows = [d for d in rows if d.category in categories]
		if mandatory_only:
			rows = [d for d in rows if d.is_mandatory]

		pending = []
		for d in rows:
			missing = [
				text
				for text, value in (
					(_("Received"), d.received),
					(_("Verified"), d.verified),
					(_("Attachment"), d.attachment),
				)
				if not value
			]
			if missing:
				pending.append("{0} ({1})".format(d.document_name, ", ".join(missing)))
		return pending

	# ------------------------------------------------------------------
	# gates -- enforced in validate() so they hold however the status changed
	# (workflow action, API write or bulk edit)
	# ------------------------------------------------------------------
	def validate_state_requirements(self):
		if self.state_index() > 0:
			missing = [
				label
				for label, value in (
					(_("Payment Terms"), self.payment_terms_template),
					(_("Incoterm"), self.incoterm),
				)
				if not value
			]
			if missing:
				frappe.throw(
					_("Confirm the order before the shipment moves on. Missing: {0}").format(
						", ".join(missing)
					)
				)

		# Terms without an advance leg (Against BL, Against Readiness, Prior To Dispatch)
		# skip the advance stage entirely, so reaching a later stage must not demand an
		# advance that was never due.
		if self.reached("Advance Paid") and flt(self.advance_percentage) > 0:
			self.require_payment("Advance to Supplier", _("Advance to Supplier"))

		if self.reached("Balance Paid"):
			# A 100% advance leaves no balance leg to pay.
			if flt(self.advance_percentage) < FULL_ADVANCE:
				self.require_payment("Balance to Supplier", _("Balance to Supplier"))
			if flt(self.pi_value_variation) and not self.pi_variation_reason:
				frappe.throw(
					_("The final PI value differs from the value confirmed at order by {0}. "
					  "Record the reason for the variation.").format(
						frappe.utils.fmt_money(self.pi_value_variation)
					)
				)

		if self.reached("Docs Received"):
			pending = self.pending_documents(categories=("Mandatory Import Document",))
			if pending:
				frappe.throw(
					_("These mandatory import documents are not complete yet. Each needs "
					  "Received, Verified and an Attachment:<br>{0}")
					.format("<br>".join("- " + d for d in pending))
				)

		if self.reached("Forwarded to CHA") and not self.cha:
			frappe.throw(_("Set the CHA (Clearing Agent) before forwarding the documents."))

		if self.reached("Pre-Alert Received") and not self.pre_alert_date:
			frappe.throw(_("Set the Pre-Alert Date received from the CHA."))

		if self.reached("Docs Endorsed"):
			if not (self.original_docs_received or self.endorsed_on_soft_copy):
				frappe.throw(
					_("Endorsement needs either the original documents received, or "
					  "\"Cleared on Soft Copies\" ticked (SOP step 13).")
				)
			if not self.docs_endorsed_date:
				frappe.throw(_("Set the date the documents were endorsed."))

		if self.reached("BOE Checklist Confirmed"):
			pending = self.pending_documents(
				categories=("Mandatory Import Document", "BOE Checklist")
			)
			if pending:
				frappe.throw(
					_("Cannot confirm the BOE checklist. These documents are not complete yet. "
					  "Each needs Received, Verified and an Attachment:<br>{0}").format(
						"<br>".join("- " + d for d in pending)
					)
				)

		if self.reached("Duty Paid"):
			# The challan and its UTR are what the CHA quotes to customs, so the reference is
			# required; whether it was forwarded on to anyone is not a gate.
			self.require_payment("Customs Duty", _("Customs Duty"), need_utr=True)

		if self.reached("Cleared"):
			# SOP steps 17-19: queries raised along the way must be closed out, and any
			# delivery-order or CHA payment needs its receipt, before clearance completes.
			pending = self.pending_documents(categories=LATE_DOCUMENT_CATEGORIES)
			if pending:
				frappe.throw(
					_("Clearance cannot complete while these documents are outstanding:<br>{0}")
					.format("<br>".join("- " + d for d in pending))
				)
			for payment_type in ("Delivery Order", "CHA Charges"):
				if self.payment_rows(payment_type):
					self.require_payment(payment_type, _(payment_type), need_utr=True)

		if self.reached("In Transit"):
			# SOP step 21: complete only once the E-way bill is uploaded.
			missing = [
				label
				for label, value in (
					(_("E-Way Bill No"), self.eway_bill_no),
					(_("E-Way Bill Date"), self.eway_bill_date),
					(_("E-Way Bill Copy"), self.eway_bill_copy),
				)
				if not value
			]
			if missing:
				frappe.throw(
					_("The material cannot be dispatched to the factory yet. Missing: {0}").format(
						", ".join(missing)
					)
				)

		if self.reached("Received at Factory"):
			if not self.unloading_completed:
				frappe.throw(
					_("Tick Unloading Completed before marking the shipment received "
					  "(SOP step 24).")
				)
			if not self.purchase_receipt:
				frappe.throw(_("Link the Purchase Receipt (GRN) for the received material."))

		if self.reached("Docs Submitted to Bank"):
			missing = [
				label
				for label, value in (
					(_("Purchase Invoice"), self.purchase_invoice),
					(_("Bill of Entry"), self.bill_of_entry),
					(_("BL / AWB No"), self.bl_awb_no),
					(_("Covering Letter"), self.covering_letter),
				)
				if not value
			]
			if missing:
				frappe.throw(
					_("The invoice, BOE and BL go to the bank with a covering letter "
					  "(SOP step 25). Missing: {0}").format(", ".join(missing))
				)
			if not self.docs_submitted_to_bank:
				frappe.throw(_("Tick Docs Submitted to Bank."))

		if self.reached("Closed"):
			missing = [
				label
				for label, value in (
					(_("Bank Received Copy"), self.bank_received_copy),
					(_("Bank Copy Received On"), self.bank_received_date),
				)
				if not value
			]
			if missing:
				frappe.throw(
					_("The shipment closes only once the bank's received copy is uploaded. "
					  "Missing: {0}").format(", ".join(missing))
				)

	# ------------------------------------------------------------------
	# notifications
	# ------------------------------------------------------------------
	def notify_plant_of_dispatch(self):
		"""Sahil 07-08 / SOP step 23: the moment the E-way bill exists and the container
		leaves the port, the plant store is told so unloading can be prepared for.

		Fires once, on the first save that carries an E-way bill number.
		"""
		if self.plant_notified or not self.eway_bill_no:
			return

		settings = frappe.get_cached_doc("Import Tracker Settings")
		if not settings.notify_plant_on_eway_bill:
			return

		recipients = users_with_role(settings.store_notification_role or "Stock User")
		if not recipients:
			return

		message = _(
			"<p>Container for <b>{0}</b> is on its way from the port to the factory.</p>"
			"<p>E-Way Bill: <b>{1}</b><br>Vehicle: <b>{2}</b><br>Supplier: <b>{3}</b><br>"
			"Purchase Order: <b>{4}</b></p>"
			"<p>Please prepare for unloading -- it must be completed the day the "
			"container arrives.</p>"
		).format(
			get_link_to_form("Import Shipment", self.name),
			self.eway_bill_no,
			self.vehicle_no or _("(not provided)"),
			self.supplier or "",
			self.purchase_order or "",
		)

		try:
			frappe.sendmail(
				recipients=recipients,
				subject=_("Import container inbound to factory: {0}").format(self.name),
				message=message,
				reference_doctype=self.doctype,
				reference_name=self.name,
			)
		except Exception:
			# A site with no outgoing email account must not lose the shipment update --
			# the container is still on its way whether or not the mail went out.
			self.log_error("Import Tracker: plant intimation failed")
			frappe.msgprint(
				_("Could not email the plant about this dispatch. Tell the store directly."),
				title=_("Plant not notified"),
				indicator="orange",
			)
			return

		self.db_set("plant_notified", 1, update_modified=False)
		self.db_set("plant_notified_on", now_datetime(), update_modified=False)


def users_with_role(role):
	users = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"
	)
	return [
		u
		for u in set(users)
		if u not in ("Administrator", "Guest") and frappe.db.get_value("User", u, "enabled")
	]


def create_import_shipment(purchase_order):
	"""Create an Import Shipment for a PO, or return the existing one.

	Internal helper (no permission check) shared by the Purchase Order / Payment Entry
	auto-create hooks. Callers that run on user action should permission-check first.
	"""
	existing = frappe.db.exists("Import Shipment", {"purchase_order": purchase_order})
	if existing:
		return existing

	shipment = frappe.new_doc("Import Shipment")
	shipment.purchase_order = purchase_order
	shipment.mode = "Sea"
	shipment.insert(ignore_permissions=True)
	return shipment.name


@frappe.whitelist()
def make_import_shipment(purchase_order):
	"""Create (or open the existing) Import Shipment for an import PO. Button entry point."""
	po = frappe.db.get_value(
		"Purchase Order",
		purchase_order,
		["name", "custom_is_import", "docstatus"],
		as_dict=True,
	)
	if not po:
		frappe.throw(_("Purchase Order {0} not found").format(purchase_order))
	if not po.custom_is_import:
		frappe.throw(_("{0} is not marked as an import Purchase Order").format(purchase_order))
	if po.docstatus != 1:
		frappe.throw(_("Purchase Order {0} must be submitted first").format(purchase_order))

	if not frappe.db.exists("Import Shipment", {"purchase_order": purchase_order}):
		frappe.has_permission("Import Shipment", "create", throw=True)

	return create_import_shipment(purchase_order)


@frappe.whitelist()
def get_linked_payments(shipment):
	"""Submitted vouchers booked against this shipment, minus the ones already listed.

	Two voucher types settle an import. Supplier legs go out as Payment Entries against
	the order or the invoice; customs duty goes out as a Journal Entry against the Bill of
	Entry, which is what India Compliance's payment action on the BOE produces.

	The Payment Entry on_submit hook mirrors in only the first kind, only when allocated
	against the Purchase Order, and only while the shipment exists and its stage gates
	accept a save. Left behind are duty journals, entries allocated against the invoice,
	entries made before the shipment was opened, and mirrors a gate rolled back. This is
	the catch-up the Fetch Payments button runs.

	Returns rows for the client; nothing is written here, so the user still reviews them --
	and attaches the SWIFT copy or challan the SOP gates on -- before saving. A row may
	carry `update_row`, naming an existing Payments row to fill in rather than duplicate.
	"""
	doc = frappe.get_doc("Import Shipment", shipment)
	doc.check_permission("write")

	# Imported here, not at module level: events.payment_entry imports
	# create_import_shipment from this module, so a top-level import would be circular.
	from erpnext_import_tracker.events.payment_entry import is_fully_paid

	po = frappe.db.get_value(
		"Purchase Order",
		doc.purchase_order,
		["name", "advance_paid", "grand_total", "base_grand_total", "currency", "party_account_currency"],
		as_dict=True,
	)
	settled = bool(po) and is_fully_paid(po)

	rows = payment_entry_rows(doc, settled) + journal_entry_rows(doc, settled)
	mark_rows_to_update(doc, rows)
	rows.sort(key=lambda row: (row["payment_date"], row.get("payment_entry") or row["journal_entry"]))
	return rows


def supplier_leg(against_invoice, settled):
	"""Money against the invoice is settling it, never an advance. Against the order it is
	an advance unless the order is now paid in full -- the same read the on_submit hook
	takes, on today's figures rather than the payment date's, so the operator may still
	correct the Select on a part-paid order."""
	return "Balance to Supplier" if (against_invoice or settled) else "Advance to Supplier"


def payment_entry_rows(doc, settled):
	targets = [("Purchase Order", doc.purchase_order), ("Purchase Invoice", doc.purchase_invoice)]
	known = {row.payment_entry for row in doc.payments if row.payment_entry}

	# Grouped per voucher: one entry allocated across both the order and the invoice is
	# one payment out of the bank, so it must not land as two rows.
	allocations = {}
	for reference_doctype, reference_name in targets:
		if not reference_name:
			continue
		for ref in frappe.get_all(
			"Payment Entry Reference",
			filters={
				"docstatus": 1,
				"parenttype": "Payment Entry",
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
			},
			fields=["parent", "reference_doctype", "allocated_amount"],
		):
			if ref.parent not in known:
				allocations.setdefault(ref.parent, []).append(ref)

	if not allocations:
		return []

	rows = []
	for entry in frappe.get_all(
		"Payment Entry",
		filters={"name": ("in", list(allocations)), "docstatus": 1, "payment_type": "Pay"},
		fields=["name", "party", "party_type", "posting_date", "reference_no", "paid_amount"],
	):
		refs = allocations[entry.name]
		against_invoice = any(ref.reference_doctype == "Purchase Invoice" for ref in refs)
		rows.append(
			{
				"payment_type": supplier_leg(against_invoice, settled),
				"party": entry.party if entry.party_type == "Supplier" else None,
				"amount": sum(flt(ref.allocated_amount) for ref in refs) or flt(entry.paid_amount),
				"payment_date": entry.posting_date,
				"utr_no": entry.reference_no,
				"payment_entry": entry.name,
			}
		)
	return rows


def journal_entry_rows(doc, settled):
	"""Journal Entries against the order, the invoice or the Bill of Entry.

	The BOE reference type only exists once India Compliance is installed; on a site
	without it the duty query simply matches nothing.
	"""
	targets = [
		("Purchase Order", doc.purchase_order),
		("Purchase Invoice", doc.purchase_invoice),
		("Bill of Entry", doc.get("bill_of_entry")),
	]
	known = {row.journal_entry for row in doc.payments if row.journal_entry}

	allocations = {}
	for reference_type, reference_name in targets:
		if not reference_name:
			continue
		for ref in frappe.get_all(
			"Journal Entry Account",
			filters={
				"docstatus": 1,
				"parenttype": "Journal Entry",
				"reference_type": reference_type,
				"reference_name": reference_name,
			},
			fields=["parent", "reference_type", "party", "party_type", "debit_in_account_currency"],
		):
			if ref.parent not in known:
				allocations.setdefault(ref.parent, []).append(ref)

	if not allocations:
		return []

	rows = []
	for entry in frappe.get_all(
		"Journal Entry",
		filters={"name": ("in", list(allocations)), "docstatus": 1},
		fields=["name", "posting_date", "cheque_no"],
	):
		refs = allocations[entry.name]
		# The debit leg is the payment -- it clears the payable. A credit against the same
		# reference is the accrual or a reversal, not money leaving the bank.
		amount = sum(flt(ref.debit_in_account_currency) for ref in refs)
		if not amount:
			continue

		duty = any(ref.reference_type == "Bill of Entry" for ref in refs)
		against_invoice = any(ref.reference_type == "Purchase Invoice" for ref in refs)
		rows.append(
			{
				"payment_type": "Customs Duty" if duty else supplier_leg(against_invoice, settled),
				# Duty is paid to customs, so these journal rows carry no supplier party.
				"party": next(
					(ref.party for ref in refs if ref.party_type == "Supplier" and ref.party), None
				),
				"amount": amount,
				"payment_date": entry.posting_date,
				"utr_no": entry.cheque_no,
				"journal_entry": entry.name,
			}
		)
	return rows


def mark_rows_to_update(doc, rows):
	"""Point a fetched duty journal at the row the Bill of Entry hook already seeded.

	That hook writes the duty amount off the BOE with the BOE name in Remarks and no
	voucher link, because the payment had not been made yet. Appending the journal as a
	second row would double the duty on the shipment, so the journal fills in the seeded
	row instead.
	"""
	seeded = [
		row.name
		for row in doc.payments
		if row.payment_type == "Customs Duty"
		and not row.journal_entry
		and row.remarks
		and row.remarks == doc.get("bill_of_entry")
	]

	for row in rows:
		if row["payment_type"] == "Customs Duty" and seeded:
			row["update_row"] = seeded.pop(0)
