# Reviewed historical stock imports

Use **Administration → Import dated stock history** while connected to the
server. This workflow requires product-management rights plus Receive/Opening
or Issue rights for each selected type. It adds dated movements to existing
products. Use the separate product import first when products do not exist.

## Supported source shape

UTF-8 CSV or `.xlsx`, under 2 MB, with one transaction per row and at most
200 reviewed rows per batch. Choose the worksheet and physical header row.
Map your own column names; the following names are detected automatically:

```csv
Item,Date,Type,Quantity,Notes
Rice,2026-11-01,receive,20,Delivery
Rice,2026-11-02,issue,15,Kitchen
```

Required mappings: a product name or code, Date, Type and Quantity. Notes are
optional. Recognized types are opening/opening stock, receive/received/receipt,
and issue/issued, ignoring case. Product codes take precedence for matching;
ambiguous or missing matches require an explicit product choice.

Wide monthly worksheets with days across columns are not automatically converted.
Prepare a one-transaction-per-row sheet or CSV. The importer does not guess
dates, units, opening balances or transaction directions from company layouts.

## Inspect, map, review

Read the columns, choose the mapping, then choose an explicit source date
format: ISO year-month-day, day/month/year, month/day/year, or Excel numeric
dates using the 1900 or 1904 calendar. Excel's nonexistent 1900-02-29 is rejected.
Serial fractions are rejected rather than silently discarding time-of-day.
The chosen source calendar must match the workbook.

Preview does not change inventory. It shows the matched product, date, type,
quantity and notes for each row. You can correct those values before apply.
Invalid, ambiguous, repeated and already-imported rows default to Skip.
Confirm every selected row. Similar existing movements are possible duplicates,
not automatically merged transactions. To import a separate legitimate matching
movement, explicitly acknowledge the duplicate warning. This is checked again
when applying, including equal quantities written with different decimal formats.

## Apply and recovery

Applying creates a verified protected backup, then commits the selected entries
together. Any invalid opening, excess precision, future date, closed month,
pre-opening date or negative historical balance rolls back the whole selection.
Skipped rows are ignored. The existing unit and precision are never changed.

File bytes, worksheet number and physical row number determine stable source-row
UUIDs. Retrying the same file therefore cannot silently duplicate its rows,
even in concurrent imports. A changed payload under an already-used row ID is
rejected, not used to rewrite history. Use the normal correction workflow to
change a transaction already imported. A modified/resaved/reordered file can
have new IDs, so possible-duplicate review still matters.

Audit records preserve imported entry values, the filename and selected source
row numbers. The original file is not automatically archived in the database;
retain your source workbook separately when needed. No formulas or macros are
executed. Cached formula values in a workbook may be stale, so review source
values before importing.

The data-layer checks pass automated tests. This new review screen and its full
browser request flow still require real Windows browser acceptance testing.
