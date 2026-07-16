# ERPNext Import Tracker

Purchaser-side import shipment tracking for ERPNext (India). Covers the gap
between vendor shipping documents and material receipt: CHA follow-up,
pre-alert, BOE checklist verification, customs duty, document endorsement,
clearance, and factory delivery.

Designed to work alongside the [India Compliance](https://github.com/resilient-tech/india-compliance)
app (Bill of Entry, IGST on imports, e-Way Bill against BOE).

## What it adds

- **Import Shipment** DocType — one tracker per shipment, linked to Purchase
  Order / Purchase Invoice, with BL/AWB, COO, pre-alert, ETA, vehicle and
  e-way bill details, plus a **BOE Checklist** child table (auto-populated,
  conditional End Use Declaration row).
- **Workflow** `Import Shipment Process` mirroring the physical process:
  Docs Received → Forwarded to CHA → Pre-Alert Received → BOE Checklist
  Confirmed → Duty Paid → Under Clearance → Docs Endorsed → Cleared →
  In Transit → Received at Factory. Checklist confirmation is blocked until
  every row is received + verified.
- **Automation**
  - Balance Payment Entry against a Purchase Order flagged **Is Import**
    (custom field added on install) auto-creates a draft Import Shipment.
  - Purchase Receipt submit auto-closes the linked shipment as
    Received at Factory.
  - Daily reminder email (configurable in **Import Tracker Settings**) when
    pre-alert was received but the BOE checklist is unconfirmed for N days.

## Install

```bash
bench get-app https://github.com/smuriksolutions/erpnext_import_tracker
bench --site yoursite install-app erpnext_import_tracker
bench --site yoursite migrate
```

## Usage

1. Tick **Is Import** on import Purchase Orders.
2. Advance + balance Payment Entries as usual — the balance payment creates
   the Import Shipment automatically (or create one manually).
3. Work the shipment through the workflow; attach BL/AWB, COO, SWIFT copies
   etc. on the shipment.
4. On arrival: Bill of Entry (India Compliance) → Purchase Receipt → Landed
   Cost Voucher. The Purchase Receipt closes the shipment.

Kanban: open the Import Shipment list → Kanban → group by **Status**.

## License

MIT
