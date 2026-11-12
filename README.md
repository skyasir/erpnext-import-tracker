# ERPNext Import Tracker

Purchaser-side import shipment tracking for ERPNext (India), following the
Import Process SOP end to end: vendor enquiry through payment, shipment,
customs clearance and bank closure.

Designed to work alongside the [India Compliance](https://github.com/resilient-tech/india-compliance)
app (Bill of Entry, IGST on imports, e-Way Bill against BOE).

Standard ERPNext carries the parts it already models — RFQ, Supplier Quotation,
Purchase Order, Incoterm, Payment Terms Template, Payment Entry, Bill of Entry,
Purchase Receipt. This app covers only what sits between them.

## What it adds

- **Import Shipment** DocType — one tracker per shipment, linked to the
  Purchase Order / Purchase Invoice / Bill of Entry / Purchase Receipt.
  - **Documents** table — the four mandatory import documents (invoice,
    packing list, COO, BL/AWB) plus BOE checklist items and anything customs
    or the shipping line asks for later. A `Category` column decides which
    stage gate checks each row.
  - **Payments** table — advance, balance, customs duty, CHA, delivery order.
    Each row carries its UTR, its proof (SWIFT copy / receipt / challan) and
    whether those details were sent to the vendor.
  - Incoterm, Named Place and Payment Terms Template are inherited from the
    purchase order, editable on the shipment.

- **Workflow** `Import Shipment Process`, in SOP order:

  | # | State | SOP step |
  |---|---|---|
  | 0 | Awaiting Shipment | 4–6 rate check, terms, order confirmation |
  | 1 | Advance Paid | 7–8 |
  | 2 | Balance Paid | 9 |
  | 3 | Docs Received | 11 |
  | 4 | Forwarded to CHA | 10–11 |
  | 5 | Pre-Alert Received | 12 |
  | 6 | Docs Endorsed | 13 |
  | 7 | BOE Checklist Confirmed | 14 |
  | 8 | Duty Paid | 15 |
  | 9 | Under Clearance | 16–19 |
  | 10 | Cleared | 20 |
  | 11 | In Transit | 21–22 |
  | 12 | Received at Factory | 23–24 |
  | 13 | Docs Submitted to Bank | 25 |
  | 14 | Closed | 25 |

  Terms with no advance leg (Against BL, Against Readiness, Prior To Dispatch)
  skip straight from Awaiting Shipment to Balance Paid.

- **Section visibility follows the workflow state.** A read-only `stage_index`
  tracks where the shipment is, and each section reveals itself at the stage
  that needs it, so a user sees only what their stage requires.

- **Stage gates**, enforced in `validate()` so they hold however the status
  changed — workflow action, API write or bulk edit. Among them: advance and
  balance payments need their SWIFT copy; customs duty needs its UTR recorded
  *and* marked sent to the vendor; dispatch needs the e-way bill uploaded;
  receipt needs unloading confirmed and the GRN linked; closure needs the
  bank's received copy.

- **Automation**
  - Submitting a Purchase Order flagged **Is Import** creates the shipment.
  - Payment Entries against that order are mirrored into the Payments table.
  - A submitted Bill of Entry links back and seeds the duty payment row.
  - Submitting the Purchase Receipt advances the shipment to Received at
    Factory. A shipment that cannot meet its gates never blocks the GRN.
  - The factory store is emailed the moment the e-way bill is recorded, so
    unloading can be prepared for (configurable in **Import Tracker Settings**).
  - Daily reminder when pre-alert was received but the BOE checklist is
    unconfirmed for N days.
  - Items flagged **Is Import Item** must name a default supplier, and mark
    their purchase orders as imports automatically.

## Install

```bash
bench get-app https://github.com/skyasir/erpnext-import-tracker
bench --site yoursite install-app erpnext_import_tracker
bench --site yoursite migrate
bench restart
```

## Upgrading

`bench migrate` alone is **not** sufficient for this release. It adds a DocType
and rewrites a controller, so the Python processes must reload:

```bash
bench --site yoursite migrate
bench --site yoursite clear-cache
bench restart            # dev: stop and start the bench
```

Skipping the restart leaves the web worker executing the previous version of
the controller, which fails on fields the migration has already removed.

The migration patch rebuilds the workflow's states and transitions wholesale,
folds the old `coo_received` checkbox into the Certificate of Origin document
row, and removes a superseded Supplier custom field. **It is not reversible —
take a database backup first.**

## Usage

1. Tick **Is Import** on import Purchase Orders (automatic if any line item is
   flagged **Is Import Item**). Set the Incoterm and Payment Terms Template.
2. Submit the order — the Import Shipment is created.
3. Record the advance % agreed. Payment Entries appear in the Payments table;
   attach the SWIFT copy and tick *Sent to Vendor*.
4. Work the shipment through the workflow. Each section appears as its stage
   arrives; each gate says what is missing.
5. On arrival: Bill of Entry (India Compliance) → Purchase Receipt → Landed
   Cost Voucher, then submit documents to the bank and upload the bank's
   received copy to close.

Kanban: open the Import Shipment list → Kanban → group by **Status**.

## Tests

Run from the bench's `sites` directory:

```bash
../env/bin/python ../apps/erpnext_import_tracker/tests/test_section_visibility.py
../env/bin/python ../apps/erpnext_import_tracker/tests/uat_sop_flow.py
../env/bin/python ../apps/erpnext_import_tracker/tests/test_hooks_and_workflow.py
```

All three roll back and commit nothing. `create_live_transaction.py` builds one
real transaction end to end and *does* commit, for inspection in the browser;
`cleanup_demo_transaction.py` removes it again (reports by default, deletes
with `--commit`).

The suites assume a site named `supreme.localhost`; edit the `frappe.init` line
to run them elsewhere.

## License

MIT

<!-- portfolio:start -->
---

<p align="center">
  <a href="https://yasirshaikh.in/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://yasirshaikh.in/files/yasir-logo-on-dark.svg">
      <img src="https://yasirshaikh.in/files/yasir-logo.svg" alt="Yasir Shaikh" height="64">
    </picture>
  </a>
</p>

<p align="center">
  Built by <a href="https://yasirshaikh.in/">Yasir Shaikh</a>, ERPNext consultant and Frappe developer.<br>
  <a href="https://yasirshaikh.in/">See more of my work</a>
</p>
<!-- portfolio:end -->
