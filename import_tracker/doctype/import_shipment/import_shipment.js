frappe.ui.form.on("Import Shipment", {
	refresh(frm) {
		const colors = {
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

		if (!frm.is_new() && frm.doc.purchase_invoice && frappe.boot.versions?.india_compliance) {
			frm.add_custom_button(__("Bill of Entry"), () => {
				frappe.new_doc("Bill of Entry", { purchase_invoice: frm.doc.purchase_invoice });
			}, __("Create"));
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
