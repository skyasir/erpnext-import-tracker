frappe.ui.form.on("Import Shipment", {
	refresh(frm) {
		const colors = {
			"Awaiting Shipment": "gray",
			"Advance Paid": "orange",
			"Balance Paid": "orange",
			"Docs Received": "orange",
			"Forwarded to CHA": "orange",
			"Pre-Alert Received": "yellow",
			"Docs Endorsed": "yellow",
			"BOE Checklist Confirmed": "blue",
			"Duty Paid": "blue",
			"Under Clearance": "purple",
			Cleared: "green",
			"In Transit": "green",
			"Received at Factory": "green",
			"Docs Submitted to Bank": "green",
			Closed: "darkgrey",
		};
		if (frm.doc.status) {
			frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "gray");
		}

		if (frm.is_new()) {
			return;
		}

		if (frm.doc.purchase_receipt) {
			frm.add_custom_button(__("Purchase Receipt"), () => {
				frappe.set_route("Form", "Purchase Receipt", frm.doc.purchase_receipt);
			});
		}

		if (!frappe.boot.versions?.india_compliance) {
			return;
		}

		if (frm.doc.bill_of_entry) {
			frm.add_custom_button(__("Bill of Entry"), () => {
				frappe.set_route("Form", "Bill of Entry", frm.doc.bill_of_entry);
			});
		} else if (frm.doc.purchase_invoice) {
			frm.add_custom_button(
				__("Bill of Entry"),
				() => {
					frappe.model.open_mapped_doc({
						method: "india_compliance.gst_india.doctype.bill_of_entry.bill_of_entry.make_bill_of_entry",
						source_name: frm.doc.purchase_invoice,
					});
				},
				__("Create")
			);
		}
	},

	fetch_payments(frm) {
		frappe.call({
			method:
				"erpnext_import_tracker.import_tracker.doctype.import_shipment.import_shipment.get_linked_payments",
			args: { shipment: frm.doc.name },
			freeze: true,
			freeze_message: __("Fetching payments..."),
			callback(r) {
				const fetched = r.message || [];
				// The server skips what the saved doc already lists; this skips what a
				// previous click added but has not been saved yet.
				const listed = new Set(
					(frm.doc.payments || [])
						.flatMap((row) => [row.payment_entry, row.journal_entry])
						.filter(Boolean)
				);
				const rows = fetched.filter(
					(row) => !listed.has(row.payment_entry) && !listed.has(row.journal_entry)
				);

				if (!rows.length) {
					frappe.msgprint({
						title: __("Nothing to fetch"),
						message: __(
							"No further submitted Payment Entries or Journal Entries are booked against this shipment's Purchase Order, Purchase Invoice or Bill of Entry."
						),
						indicator: "orange",
					});
					return;
				}

				let added = 0;
				let updated = 0;
				rows.forEach((row) => {
					const { update_row, ...values } = row;
					const seeded =
						update_row && (frm.doc.payments || []).find((r) => r.name === update_row);
					if (!seeded) {
						frm.add_child("payments", values);
						added++;
						return;
					}
					// Fill the blanks on the row the Bill of Entry hook seeded -- never
					// overwrite a figure someone has already keyed in.
					Object.keys(values).forEach((field) => {
						if (values[field] && !seeded[field]) {
							frappe.model.set_value(seeded.doctype, seeded.name, field, values[field]);
						}
					});
					updated++;
				});

				frm.refresh_field("payments");
				const summary = [];
				if (added) summary.push(__("{0} added", [added]));
				if (updated) summary.push(__("{0} updated", [updated]));
				frappe.show_alert({
					message: __("Payments: {0}. Attach the proof on each row, then save.", [
						summary.join(", "),
					]),
					indicator: "green",
				});
			},
		});
	},

	purchase_order(frm) {
		if (frm.doc.purchase_order && !frm.doc.purchase_invoice) {
			frappe.db
				.get_list("Purchase Invoice Item", {
					filters: { purchase_order: frm.doc.purchase_order, docstatus: 1 },
					fields: ["parent"],
					limit: 1,
				})
				.then((r) => {
					if (r.length) frm.set_value("purchase_invoice", r[0].parent);
				});
		}
	},

	incoterm(frm) {
		// SOP section 4: under CIF/CIP the vendor already carries the insurance.
		if (["CIF", "CIP"].includes(frm.doc.incoterm) && frm.doc.insurance_required) {
			frm.set_value("insurance_required", 0);
		}
	},

	pi_value_final(frm) {
		frm.set_value(
			"pi_value_variation",
			flt(frm.doc.pi_value_final) - flt(frm.doc.pi_value_confirmed)
		);
	},
});
