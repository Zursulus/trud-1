# VTB Template 6 exchange

Source evidence is the real VTB Template 6 package received by TSN. Original bank files remain outside the public repository; repository tests use synthetic values.

## Accepted-payments registry (VTB → TSN)

The first production stage is **read-only dry-run only**.

Matching rule:
1. exact non-empty `Account.number == personal_account`;
2. FIO and address from the bank file are display/diagnostic evidence only;
3. no fuzzy/name/address matching may create or select an account;
4. unmatched rows remain unmatched and require human correction of master data.

Validation:
- supported encodings: UTF-8, WIN-1251, KOI8-R;
- semicolon delimiter;
- at least 12 leading fields per payment row;
- final `=` control row is mandatory;
- row count and operation/transfer/commission totals must reconcile exactly with `Decimal`;
- date/time, instrument, UNI, account and MMYY period are validated fail-closed.

Future write-path idempotency:
- canonical external operation key: `UNI + bank_document + paid_on + operation_amount`;
- duplicate keys within one uploaded file are already surfaced by dry-run;
- before any write implementation, persist a dedicated bank-import identity/audit record with a database uniqueness constraint for that key (or an equivalently strong VTB-provided identifier);
- do not use FIO/address as idempotency keys;
- a repeated file/operation must produce `already imported`, never a second Payment.

Future audit requirement:
- preserve source filename/hash, detected encoding, control totals, row external identity, exact matched account id, import actor/time and resulting Payment id;
- raw bank file must not be committed to the public repository;
- malformed/unmatched/duplicate rows must remain reviewable without partial silent import.

## Debt registry (TSN → VTB)

Format: `personal account;FIO;address;MMYY;amount`, semicolon-separated, WIN-1251.

Export must use explicit canonical account/contact data and must skip/report missing or over-limit fields instead of inventing them.
Bank-ready filename requires the actual TSN INN, settlement account and VTB service code configuration; until then a TEST filename must be explicit.
