"""Prepare Excel's native query definition and a disposable registration macro.

No credentials are embedded. The macro lives in a separate temporary workbook;
the delivered .xlsx remains macro-free. A synthetic in-memory query checks Excel
calculation before the production URL is installed, without contacting Render.
"""
import json
from pathlib import Path
import sys


def main(plan_path):
    plan=json.loads(plan_path.read_text(encoding="utf-8"))
    folder=plan_path.parent
    columns=", ".join(json.dumps(column) for column in plan["columns"])
    types=", ".join("{"+json.dumps(column)+", "+("type number" if column[0] in "RI" and len(column)==3 else "type text")+"}" for column in plan["columns"])
    production=f'''let
    Source = OData.Feed({json.dumps(plan["feed_url"])}, null, [Implementation="2.0", Timeout=#duration(0,0,3,0)]),
    Daily = Source{{[Name="Daily",Signature="table"]}}[Data],
    Buffered = Table.Buffer(Daily),
    Typed = Table.TransformColumnTypes(Buffered, {{{types}}}),
    Ordered = Table.Sort(Typed, {{{{"Key", Order.Ascending}}}}),
    Result = Table.ReorderColumns(Ordered, {{{columns}}})
in
    Result
'''
    query_file=folder/"InventoryDaily.m"
    query_file.write_text(production,encoding="ascii")
    keys=", ".join(json.dumps(key) for key in plan["keys"])
    fixture=f'''let
    Keys = {{{keys}}},
    Columns = {{{columns}}},
    Rows = List.Transform(Keys, (k) => Record.FromList({{k, if k="dry-foods:7:2026-11" then "Linked" else "Not linked", "fixture-product", "LOCAL TEST DATA", "PKT", "2026-11-06T00:00:00Z"}} & List.Transform({{1..62}}, (n) => if k="dry-foods:7:2026-11" and n=1 then 2.5 else if k="dry-foods:7:2026-11" and n=32 then 1 else null), Columns)),
    Table = Table.FromRecords(Rows),
    Typed = Table.TransformColumnTypes(Table, {{{types}}})
in
    Typed
'''
    fixture_file=folder/"native-fixture.m"
    fixture_file.write_text(fixture,encoding="ascii")
    native_log=folder/"native-excel-check.json"
    module=f'''Attribute VB_Name = "InventoryConnectionSetup2"
Option Explicit
Private Function ReadQuery(ByVal filename As String) As String
    Dim handle As Integer
    handle = FreeFile
    Open filename For Binary Access Read As #handle
    ReadQuery = Space$(LOF(handle))
    Get #handle, , ReadQuery
    Close #handle
End Function

Public Sub RegisterInventoryRefresh2()
    Dim wb As Workbook, data As Worksheet, table As QueryTable
    Dim oldCalc As XlCalculation, oldAlerts As Boolean, handle As Integer
    Dim fixture As String, production As String, receipt As Double, issue As Double, closing As Double, failure As String
    On Error GoTo Failed
    oldCalc = Application.Calculation
    oldAlerts = Application.DisplayAlerts
    Application.DisplayAlerts = False
    Application.Calculation = xlCalculationManual
    fixture = ReadQuery("{fixture_file}")
    production = ReadQuery("{query_file}")
    Set wb = Workbooks.Open("{plan["output"]}", UpdateLinks:=0, ReadOnly:=False)
    Set data = wb.Worksheets("App Data")
    wb.Queries.Add Name:="InventoryDaily", Formula:=fixture, Description:="Read-only daily app receiving and issuing. Opening stock remains manual."
    Set table = data.QueryTables.Add(Connection:="OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=InventoryDaily;Extended Properties=" & Chr$(34) & Chr$(34), Destination:=data.Range("A1"), Sql:="SELECT * FROM [InventoryDaily]")
    table.Name = "InventoryDaily"
    With table
        .CommandType = xlCmdSql
        .CommandText = Array("SELECT * FROM [InventoryDaily]")
        .RowNumbers = False
        .FillAdjacentFormulas = False
        .PreserveFormatting = True
        .RefreshOnFileOpen = False
        .BackgroundQuery = False
        .RefreshStyle = xlOverwriteCells
        .SavePassword = False
        .SaveData = True
        .AdjustColumnWidth = False
        .RefreshPeriod = 0
        .Refresh BackgroundQuery:=False
    End With
    wb.Worksheets("Nov 2026 Dry Foods").Range("E7").Value2 = 10
    Application.CalculateFullRebuild
    receipt = wb.Worksheets("Nov 2026 Dry Foods").Range("AK7").Value2
    issue = wb.Worksheets("Nov 2026 Dry Foods").Range("BX7").Value2
    closing = wb.Worksheets("Nov 2026 Dry Foods").Range("BY7").Value2
    If Abs(receipt - 2.5) > 0.0000001 Or Abs(issue - 1) > 0.0000001 Or Abs(closing - 11.5) > 0.0000001 Then Err.Raise 513, , "The native Excel calculation test did not match expected totals."
    If Abs(wb.Worksheets("Dec 2026 Dry Foods").Range("E7").Value2 - 11.5) > 0.0000001 Then Err.Raise 514, , "Monthly carry-forward failed the native Excel check."
    ' Remove ALL fixture values and restore the user's blank opening cell.
    wb.Worksheets("Nov 2026 Dry Foods").Range("E7").ClearContents
    data.Range("A2:BP{len(plan["keys"])+1}").ClearContents
    wb.Queries("InventoryDaily").Formula = production
    Application.CalculateFullRebuild
    wb.Worksheets("Overview").Activate
    wb.Save
    handle = FreeFile
    Open "{native_log}" For Output As #handle
    Print #handle, "{{""query_registered"":true,""native_refresh_test_passed"":true,""receipt"":" & CStr(receipt) & ",""issue"":" & CStr(issue) & ",""closing"":" & CStr(closing) & ",""fixture_cleared"":true}}"
    Close #handle
    wb.Close SaveChanges:=False
    Application.Calculation = oldCalc
    Application.DisplayAlerts = oldAlerts
    MsgBox "Excel connection ready. Native refresh and monthly carry-forward tests passed. Test quantities were cleared and opening stock is blank.", vbInformation
    Exit Sub
Failed:
    failure = Err.Description
    On Error Resume Next
    If Not wb Is Nothing Then wb.Close SaveChanges:=False
    Application.Calculation = oldCalc
    Application.DisplayAlerts = oldAlerts
    MsgBox "Excel setup stopped without saving test data: " & failure, vbExclamation
End Sub
'''
    (folder/"InventoryConnectionSetup.bas").write_text(module,encoding="ascii")
    print("Prepared a credential-free OData query and a disposable native Excel calculation check.")


if __name__=="__main__":main(Path(sys.argv[1]).resolve())
