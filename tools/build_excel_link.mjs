/* Artifact Tool authors the new input formulas and supporting data worksheet.
   The source is never saved. A preservation pass later copies only these
   authored cells back into the original package, retaining native Excel parts.
   Run with bundled Node; INVENTORY_RUNTIME_MODULES is the loader's module path. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(path.join(process.env.INVENTORY_RUNTIME_MODULES,'excel-link-builder.cjs'));
const {FileBlob,SpreadsheetFile}=require('@oai/artifact-tool');
const planPath=path.resolve(process.argv[2]);
const plan=JSON.parse(await fs.readFile(planPath,'utf8'));
const outputDir=path.dirname(planPath);
const reviewed=path.join(outputDir,'layout-reviewed.flag');
await fs.rm(reviewed,{force:true});
console.log('Reading the original 61-sheet workbook; no source writes.');
const workbook=await SpreadsheetFile.importXlsx(await FileBlob.load(plan.source));
console.log('Original workbook imported. Rendering the unchanged layout.');
for(const [sheetName,range,file] of [
  ['Overview','B2:G17','original-overview.png'],
  ['Nov 2026 Dry Foods','A1:M10','original-food.png'],
  ['Nov 2026 Disposables','A1:L10','original-disposable.png'],
  ['Nov 2026 Chemicals','A1:L10','original-chemicals.png'],
  ['Nov 2026 Stationery','A1:L10','original-stationery.png'],
  ['Nov 2026 Cutlery & Crockery','A1:M10','original-cutlery.png']]) {
  const image=await workbook.render({sheetName,range,scale:1,format:'png'});
  await fs.writeFile(path.join(outputDir,file),new Uint8Array(await image.arrayBuffer()));
}
console.log('Layout previews ready. Waiting for the local visual review checkpoint.');
while(true) {
  try {await fs.access(reviewed);break;} catch {await new Promise(resolve=>setTimeout(resolve,500));}
}

function column(number) {
  let result='';
  while(number){const rem=(number-1)%26;result=String.fromCharCode(65+rem)+result;number=Math.floor((number-1)/26);}
  return result;
}
const data=workbook.worksheets.add('App Data');
const last=plan.keys.length+1;
data.getRange('A1:BR1').values=[[...plan.columns,'Link check','Expected workbook row']];
data.getRange('BR2:BR'+last).values=plan.keys.map(key=>[key]);
data.getRange('BQ2:BQ'+last).formulas=plan.keys.map((_,i)=>{
  const row=i+2;
  return [`=IF(A${row}="","Not refreshed",IF(A${row}<>BR${row},"Refresh order mismatch",IF(B${row}="Linked","Linked",B${row})))`];
});
data.getRange('A1:BR1').format={fill:'#17543e',font:{bold:true,color:'#ffffff'},rowHeight:28};
data.getRange('A:A').format.columnWidth=34;
data.getRange('B:B').format.columnWidth=22;
data.getRange('C:D').format.columnWidth=38;
data.getRange('E:E').format.columnWidth=12;
data.getRange('F:F').format.columnWidth=32;
data.getRange('G:BP').format.columnWidth=12;
data.getRange('BQ:BR').format.columnWidth=34;
data.freezePanes.freezeRows(1);
let authored=0;
for(let index=0;index<plan.sheets.length;index++) {
  const definition=plan.sheets[index];
  const sheet=workbook.worksheets.getItem(definition.name);
  const first=definition.rows[0].row;
  const end=definition.rows.at(-1).row;
  if(end-first+1!==definition.rows.length)throw Error('Unexpected spacer among product rows: '+definition.name);
  for(const [start,dataStart] of [[definition.receive_start,7],[definition.issue_start,38]]) {
    const formulas=definition.rows.map(item=>Array.from({length:definition.days},(_,day)=>
      `=IF('App Data'!$BQ${item.data_row}="Linked",'App Data'!${column(dataStart+day)}${item.data_row},"")`));
    sheet.getRange(`${column(start)}${first}:${column(start+definition.days-1)}${end}`).formulas=formulas;
    authored+=definition.rows.length*definition.days;
  }
  if((index+1)%5===0)console.log(`Linked daily cells on ${index+1}/${plan.sheets.length} worksheets.`);
}
const overview=workbook.worksheets.getItem('Overview');
overview.getRange('B9').values=[['2. Use Data > Refresh All to read daily receiving and issuing from the app.']];
overview.getRange('B31').values=[['APP DAILY RECEIVING & ISSUING']];
overview.getRange('B32').formulas=[['=IF(\'App Data\'!A2="","Not refreshed yet","Last successful refresh (UTC): "&\'App Data\'!F2)']];
overview.getRange('B33').formulas=[[`=IF('App Data'!A2="","Product links not loaded",COUNTIF('App Data'!BQ2:BQ${last},"Linked")/${plan.profile.months}&" of ${plan.profile.items.length} product rows linked")`]];
overview.getRange('B34').values=[['Refresh after app entries synchronize. Opening stock remains yours to enter.']];
console.log(`Authored ${authored} daily quantity formulas; original calculation formulas and openings were not edited.`);
workbook.recalculate();
console.log('Recalculation complete. Exporting the authored workbook cells.');
const exported=await SpreadsheetFile.exportXlsx(workbook);
await exported.save(path.join(outputDir,'artifact-authored-cells.xlsx'));
console.log('Artifact authoring complete. Ready for native-feature preservation and query registration.');
