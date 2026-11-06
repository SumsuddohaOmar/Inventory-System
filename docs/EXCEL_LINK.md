# Refresh daily inventory quantities in Excel

The prepared workbook reads **daily receiving and daily issuing** from the
cloud app. Its existing formulas calculate totals, closing stock and the next
month's opening. It does not change app data or import opening stock from the
app. Enter the first November opening quantities yourself in Excel.

## First setup, after this app update is deployed

1. Save the prepared **App Daily Link** workbook and **Excel connection
   setup.json** somewhere you can find them. Keep your original workbook.
2. Sign in to the Render inventory. Open **Settings → Excel daily receiving &
   issuing connection**. Choose the supplied JSON file, then **Create Excel
   connection**. This registers the workbook's product rows; it does not create
   products, change stock or upload the entire workbook.
3. Review the product links. Names, category, unit and packing/specification
   help match existing products. Ambiguous or missing products stay unlinked.
   Choose the correct workbook row and app product, then save the link. Use
   **Remove selected link** to undo a mistaken match. One app product cannot
   fill two separate workbook product rows. After adding an app product, use
   **Match newly added products**, then review again.
4. Open the prepared workbook in desktop Excel and choose **Data → Refresh
   All**. If Excel asks for connection credentials, select **Basic**. Username:
   `excel`. Copy the separate **Excel connection password** shown in Settings
   into Excel's password field. Use the displayed OData address as the
   connection's credential scope. Never use the Neon URL or your app password.
5. Check the Overview's last successful refresh and linked-product count.
   Check a known receipt and issue against the app before relying on the file.

The password is displayed only when created or reset. If you lose it, open the
existing connection in Settings and choose **Reset Excel password**. The old
password immediately stops working. Update the saved credentials in Excel's
Data Source Settings. **Disable this connection** stops future refreshes;
quantities already saved in an Excel file remain in that file.

## Normal use

Enter receiving and issues in the app as usual. Let pending offline entries
synchronize first. Open Excel, choose **Data → Refresh All**, wait for it to
finish, then save. Refresh reads the whole prepared period again: missing a
few days of refresh does not lose those days. Corrections to saved app entries
appear on the next successful refresh, without adding the same movement twice.

This is a manual refresh connection, without a timer or refresh-on-open.
The free Render server may need time to wake up. If refresh fails, Excel can
retain the previous successful values: check the query error and the last
successful refresh time before using them. A saved workbook is a snapshot
until refreshed. It is not a complete database backup.

Only linked products with no movements receive numeric zero. Unlinked,
ambiguous or deleted-product rows remain blank. The Overview shows how many
product rows are linked; **App Data** provides individual statuses. Check
unresolved rows before treating totals as complete. Never sort, insert, delete
or overwrite rows/columns on App Data: the prepared monthly formulas use its
fixed positions and verify the row keys to prevent incorrect placement.

The supplied copy covers November 2026–October 2027, all five categories and
464 workbook product rows. App products added later need matching workbook
rows and an updated prepared connection; they are not automatically inserted
into this template. Match units carefully: quantities are not unit-converted.
Do not type daily movements over the new receiving/issuing formulas. Opening
stock, descriptive fields and all original calculations retain their existing
roles. Changing an Excel description does not update the app or remap its ID.

## Excel versions

Windows Excel 2021 supports Power Query and OData. Microsoft 365 Excel for Mac
also lists OData import/refresh. This copy was tested in native Windows Excel;
the first authenticated Mac refresh still needs checking on your Mac. Excel
in a browser is not verified for this Basic-authenticated custom feed. Use
desktop Excel to refresh and save, then view the saved file elsewhere. Mobile
Excel does not provide this Power Query refresh workflow.

Microsoft references:
- [Power Query data sources by Excel version](https://support.microsoft.com/en-us/excel/power-query-data-sources-in-excel-versions)
- [OData connector and authentication](https://learn.microsoft.com/en-us/power-query/connectors/odata-feed)

## Connection safety and developer notes

The workbook contains an HTTPS service URL and a query, without embedded
credentials or macros. Excel receives a random, revocable read-only password;
only its hash is stored on the server. It grants access only to this
connection's daily quantities. Requests recheck the owner's current active
account and Read/Reports permissions. A signed-in browser cookie alone cannot
read the feed; the Excel password cannot write stock or manage connections.

Connection profiles and reviewed mappings use the existing settings store.
No schema migration or additional paid service is needed. Production reads
use the existing serialized PostgreSQL transaction, and return a complete
snapshot in one response. The workbook buffers and sorts that response before
loading it into App Data. Openings, notes, reports and pending browser entries
are not in the feed. Existing offline synchronization is unchanged.

To prepare another copy, `tools/prepare_excel_link.py` inspects the original
template read-only and writes a build plan/manifest. The Artifact Tool builder
authors the daily links. `preserve_excel_template.py` copies only those new
cells into the original package to retain native formatting and features.
`prepare_excel_native.py` generates a disposable Excel setup/check macro;
native Excel registers the Power Query connection in the macro-free copy.
The synthetic refresh check verifies 2.5 received, 1 issued and 11.5 closing
from an opening of 10, including December carry-forward. It then clears all
test inputs, installs the credential-free production query and saves.
`check_excel_link.py` audits the saved file against its unchanged source.

Do not rebuild a workbook containing user-entered real openings or saved data
without preserving those inputs. The builder targets the supplied empty
template, not arbitrary workbooks. Generated setup macros are developer tools;
the user only needs the finished workbook, JSON setup file and these steps.
