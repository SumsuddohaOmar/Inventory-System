// Read-only visual review of the saved native workbook. No export or source writes.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(path.join(process.env.INVENTORY_RUNTIME_MODULES,'excel-link-review.cjs'));
const {FileBlob,SpreadsheetFile}=require('@oai/artifact-tool');
const plan=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const workbook=await SpreadsheetFile.importXlsx(await FileBlob.load(plan.output));
for(const [sheetName,range,file] of [
  ['Overview','B2:G35','connected-overview.png'],
  ['App Data','A1:F5','connected-data.png']]) {
  const picture=await workbook.render({sheetName,range,scale:1,format:'png'});
  await fs.writeFile(path.join(path.dirname(plan.output),file),new Uint8Array(await picture.arrayBuffer()));
}
console.log('Saved workbook views rendered for review.');
