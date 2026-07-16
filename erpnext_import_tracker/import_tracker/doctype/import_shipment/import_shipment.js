frappe.ui.form.on("Import Shipment", {
	refresh(frm) {
		const colors = {
			"Awaiting Shipment": "gray",
			"Docs Received": "orange",
			"Forwarded to CHA": "orange",
			"Pre-Alert Received": "yellow",
			"BOE Checklist Confirmed": "blue",
			"Duty Paid": "blue",
			"Under Clearance": "purple",
			"Docs Endorsed": "purple",
			"Cleared": "green",
			"In Transit": "green",
			"Received at Factory": "green",
		};
		if (frm.doc.status) {
			frm.page.set_indicator(frm.doc.status, colors[frm.doc.status] || "gray");
		}

		if (frm.is_new() || !frappe.boot.versions?.india_compliance) {
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
});
