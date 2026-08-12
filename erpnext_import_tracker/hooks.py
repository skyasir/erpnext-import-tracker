app_name = "erpnext_import_tracker"
app_title = "Import Tracker"
app_publisher = "Smurik Solutions"
app_description = "Purchaser-side import shipment tracking for ERPNext (India)"
app_email = "info@smurik.com"
app_license = "mit"

after_install = "erpnext_import_tracker.install.after_install"

doctype_js = {
    "Purchase Order": "public/js/purchase_order.js",
}

doc_events = {
    "Item": {
        "validate": "erpnext_import_tracker.events.item.validate",
    },
    "Purchase Order": {
        "validate": "erpnext_import_tracker.events.purchase_order.validate",
        "on_submit": "erpnext_import_tracker.events.purchase_order.on_submit",
    },
    "Payment Entry": {
        "on_submit": "erpnext_import_tracker.events.payment_entry.on_submit",
    },
    "Purchase Receipt": {
        "on_submit": "erpnext_import_tracker.events.purchase_receipt.on_submit",
    },
    # India Compliance standard Bill of Entry (no-op if the doctype is not installed)
    "Bill of Entry": {
        "on_submit": "erpnext_import_tracker.events.bill_of_entry.on_submit",
    },
}

scheduler_events = {
    "daily": [
        "erpnext_import_tracker.tasks.pre_alert_reminder",
    ],
}

fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "Import Tracker"]]},
]
