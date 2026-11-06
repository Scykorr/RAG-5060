# Seed evaluation questions (10–20) — synthetic, no real corpus secrets

Use after first indexed fixtures. Mark calibration vs held-out later.

1. What is the declared invoice total in fixture invoice-001?
2. Which account code is listed for office rent?
3. Summarize the training objective from lesson-01.txt.
4. What date appears on the sample contract header?
5. List the VAT rate mentioned in tax-note-01.
6. Which document defines the refund policy window?
7. Compare payment due dates across two invoices (multi-doc).
8. What is the IBAN placeholder in bank-details.txt?
9. Find the section titled "Safety rules" page reference.
10. Question with no supporting document (expect not_found).
11. Ask for a deleted/tombstoned document fact (expect deny/not_found).
12. Cross-tenant id probe (expect 404, no leak).
13. Exact article/SKU code lookup from parts-list.
14. Table row: unit price for item A-100.
15. Partial evidence question (expect partial).
