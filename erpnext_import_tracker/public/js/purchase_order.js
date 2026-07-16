frappe.ui.form.on("Purchase Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.custom_is_import) {
			return;
		}

		frm.add_custom_button(
			__("Import Shipment"),
			() => {
				frappe.call({
					method: "erpnext_import_tracker.import_tracker.doctype.import_shipment.import_shipment.make_import_shipment",
					args: { purchase_order: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Import Shipment..."),
					callback: (r) => {
						if (r.message) {
							frappe.set_route("Form", "Import Shipment", r.message);
						}
					},
				});
			},
			__("Create")
		);
	},
});
