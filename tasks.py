import frappe
from frappe.utils import add_days, today


def pre_alert_reminder():
	settings = frappe.get_single("Import Tracker Settings")
	if not settings.enable_reminders:
		return

	days = settings.pre_alert_reminder_days or 3
	cutoff = add_days(today(), -days)

	overdue = frappe.get_all(
		"Import Shipment",
		filters={
			"status": "Pre-Alert Received",
			"pre_alert_date": ["<=", cutoff],
		},
		fields=["name", "supplier", "purchase_order", "pre_alert_date", "eta"],
	)
	if not overdue:
		return

	role = settings.notification_role or "Purchase User"
	recipients = frappe.get_all(
		"Has Role",
		filters={"role": role, "parenttype": "User"},
		pluck="parent",
	)
	recipients = [
		u for u in set(recipients)
		if u not in ("Administrator", "Guest")
		and frappe.db.get_value("User", u, "enabled")
	]
	if not recipients:
		return

	rows = "".join(
		"<tr><td>{0}</td><td>{1}</td><td>{2}</td><td>{3}</td><td>{4}</td></tr>".format(
			frappe.utils.get_link_to_form("Import Shipment", d.name),
			d.supplier or "",
			d.purchase_order or "",
			frappe.utils.formatdate(d.pre_alert_date),
			frappe.utils.formatdate(d.eta) if d.eta else "-",
		)
		for d in overdue
	)
	message = (
		"<p>BOE checklist not confirmed for the following shipments, "
		"pre-alert received more than {0} day(s) ago:</p>"
		"<table class='table table-bordered'>"
		"<tr><th>Shipment</th><th>Supplier</th><th>PO</th><th>Pre-Alert</th><th>ETA</th></tr>"
		"{1}</table>"
	).format(days, rows)

	frappe.sendmail(
		recipients=recipients,
		subject="Import Tracker: {0} shipment(s) pending BOE checklist confirmation".format(
			len(overdue)
		),
		message=message,
	)
