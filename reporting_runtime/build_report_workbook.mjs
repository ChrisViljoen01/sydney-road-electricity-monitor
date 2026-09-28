import fs from 'node:fs/promises';
import path from 'node:path';
import { SpreadsheetFile, Workbook } from '@oai/artifact-tool';


const [inputPath, outputPath, previewPath] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error('Usage: node build_report_workbook.mjs input.json output.xlsx [preview.png]');
}

const data = JSON.parse(await fs.readFile(inputPath, 'utf8'));
const workbook = Workbook.create();
const summary = workbook.worksheets.add('Summary');
const daily = workbook.worksheets.add('Daily Usage');
const unusual = workbook.worksheets.add('Unusual Usage');
const solarSignals = workbook.worksheets.add('Solar Signals');

const navy = '#1C2545';
const orange = '#E04403';
const pale = '#F4F7FB';
const border = '#D6DEEA';
const muted = '#64748B';

summary.showGridLines = false;
daily.showGridLines = false;
unusual.showGridLines = false;
solarSignals.showGridLines = false;

const dailyHeaders = [
  'Date', 'Area', 'Electricity used (kWh)', 'Highest load (kW)',
  'Highest demand (kVA)', 'Unusual', 'Review level', 'Peak time', 'Data quality',
  'Meter classification',
];
const dailyRows = data.daily.map((row) => [
  new Date(`${row.date}T00:00:00Z`), row.area, row.electricityUsedKwh,
  row.peakKw, row.peakKva, row.unusual, row.reviewLevel, row.peakTime, row.dataQuality,
  row.meterClassification,
]);
daily.getRangeByIndexes(0, 0, dailyRows.length + 1, dailyHeaders.length).values = [dailyHeaders, ...dailyRows];
daily.getRange(`A1:J1`).format = {
  fill: navy,
  font: { bold: true, color: '#FFFFFF' },
  wrapText: true,
  verticalAlignment: 'center',
};
daily.getRange('A1:I1').format.rowHeight = 32;
if (dailyRows.length > 0) {
  const dailyEnd = dailyRows.length + 1;
  daily.tables.add(`A1:J${dailyEnd}`, true, 'DailyUsageTable');
  daily.getRange(`A2:A${dailyEnd}`).format.numberFormat = 'yyyy-mm-dd';
  daily.getRange(`C2:E${dailyEnd}`).format.numberFormat = '#,##0.0';
  daily.getRange(`A2:J${dailyEnd}`).format.borders = {
    insideHorizontal: { style: 'thin', color: border },
  };
  daily.getRange(`F2:F${dailyEnd}`).conditionalFormats.add('containsText', {
    text: 'Yes',
    format: { fill: '#FDECEC', font: { bold: true, color: '#B91C1C' } },
  });
}
daily.freezePanes.freezeRows(1);
const dailyFormatEnd = Math.max(2, dailyRows.length + 1);
daily.getRange(`A1:A${dailyFormatEnd}`).format.columnWidth = 13;
daily.getRange(`B1:B${dailyFormatEnd}`).format.columnWidth = 24;
daily.getRange(`C1:E${dailyFormatEnd}`).format.columnWidth = 20;
daily.getRange(`F1:G${dailyFormatEnd}`).format.columnWidth = 14;
daily.getRange(`H1:H${dailyFormatEnd}`).format.columnWidth = 22;
daily.getRange(`I1:I${dailyFormatEnd}`).format.columnWidth = 16;
daily.getRange(`J1:J${dailyFormatEnd}`).format.columnWidth = 24;

const unusualHeaders = [
  'Date', 'Area', 'Review level', 'What changed', 'What the meter shows',
  'Suggested operational check', 'Peak time', 'Status',
];
const unusualRows = data.unusual.map((row) => [
  new Date(`${row.date}T00:00:00Z`), row.area, row.reviewLevel, row.type,
  row.whatMeterShows, row.suggestedCheck, row.peakTime, row.status,
]);
unusual.getRangeByIndexes(0, 0, unusualRows.length + 1, unusualHeaders.length).values = [unusualHeaders, ...unusualRows];
unusual.getRange('A1:H1').format = {
  fill: navy,
  font: { bold: true, color: '#FFFFFF' },
  wrapText: true,
  verticalAlignment: 'center',
};
unusual.getRange('A1:H1').format.rowHeight = 32;
if (unusualRows.length > 0) {
  const unusualEnd = unusualRows.length + 1;
  unusual.tables.add(`A1:H${unusualEnd}`, true, 'UnusualUsageTable');
  unusual.getRange(`A2:A${unusualEnd}`).format.numberFormat = 'yyyy-mm-dd';
  unusual.getRange(`A2:H${unusualEnd}`).format.wrapText = true;
  unusual.getRange(`A2:H${unusualEnd}`).format.verticalAlignment = 'top';
  unusual.getRange(`A2:H${unusualEnd}`).format.borders = {
    insideHorizontal: { style: 'thin', color: border },
  };
}
unusual.freezePanes.freezeRows(1);
const unusualFormatEnd = Math.max(2, unusualRows.length + 1);
unusual.getRange(`A1:A${unusualFormatEnd}`).format.columnWidth = 13;
unusual.getRange(`B1:B${unusualFormatEnd}`).format.columnWidth = 22;
unusual.getRange(`C1:D${unusualFormatEnd}`).format.columnWidth = 16;
unusual.getRange(`E1:F${unusualFormatEnd}`).format.columnWidth = 46;
unusual.getRange(`G1:G${unusualFormatEnd}`).format.columnWidth = 22;
unusual.getRange(`H1:H${unusualFormatEnd}`).format.columnWidth = 16;

const solarSignalHeaders = [
  'Date', 'Classification', 'Generated (kWh)', 'Recent average (kWh)',
  'Difference vs usual', 'Peak output (kW)', 'Peak time', 'Verification / first check',
];
const solarSignalRows = [
  ...data.solarWarnings.map((row) => [
    new Date(`${row.date}T00:00:00Z`), `Low generation - ${row.classification}`,
    row.generatedKwh, row.usualKwh, row.differencePercent / 100,
    row.peakKw, String(row.peakTime || '').slice(11, 16), row.action,
  ]),
  ...data.solarPositive.map((row) => [
    new Date(`${row.date}T00:00:00Z`), row.classification,
    row.generatedKwh, row.usualKwh, row.differencePercent / 100,
    row.peakKw, String(row.peakTime || '').slice(11, 16), row.action,
  ]),
];
solarSignals.getRangeByIndexes(
  0, 0, solarSignalRows.length + 1, solarSignalHeaders.length,
).values = [solarSignalHeaders, ...solarSignalRows];
solarSignals.getRange('A1:H1').format = {
  fill: navy,
  font: { bold: true, color: '#FFFFFF' },
  wrapText: true,
  verticalAlignment: 'center',
};
solarSignals.getRange('A1:H1').format.rowHeight = 34;
if (solarSignalRows.length > 0) {
  const solarSignalEnd = solarSignalRows.length + 1;
  solarSignals.tables.add(`A1:H${solarSignalEnd}`, true, 'SolarSignalsTable');
  solarSignals.getRange(`A2:A${solarSignalEnd}`).format.numberFormat = 'yyyy-mm-dd';
  solarSignals.getRange(`C2:D${solarSignalEnd}`).format.numberFormat = '#,##0.0';
  solarSignals.getRange(`E2:E${solarSignalEnd}`).format.numberFormat = '+0.0%;-0.0%';
  solarSignals.getRange(`F2:F${solarSignalEnd}`).format.numberFormat = '#,##0.0';
  solarSignals.getRange(`A2:H${solarSignalEnd}`).format.wrapText = true;
  solarSignals.getRange(`A2:H${solarSignalEnd}`).format.verticalAlignment = 'top';
  solarSignals.getRange(`A2:H${solarSignalEnd}`).format.borders = {
    insideHorizontal: { style: 'thin', color: border },
    insideVertical: { style: 'thin', color: border },
  };
  solarSignals.getRange(`B2:B${solarSignalEnd}`).conditionalFormats.add('containsText', {
    text: 'Positive',
    format: { fill: '#E8F6F3', font: { bold: true, color: '#00695C' } },
  });
  solarSignals.getRange(`B2:B${solarSignalEnd}`).conditionalFormats.add('containsText', {
    text: 'Low generation',
    format: { fill: '#FDECEC', font: { bold: true, color: '#B91C1C' } },
  });
}
solarSignals.freezePanes.freezeRows(1);
const solarSignalFormatEnd = Math.max(2, solarSignalRows.length + 1);
solarSignals.getRange(`A1:A${solarSignalFormatEnd}`).format.columnWidth = 13;
solarSignals.getRange(`B1:B${solarSignalFormatEnd}`).format.columnWidth = 24;
solarSignals.getRange(`C1:F${solarSignalFormatEnd}`).format.columnWidth = 20;
solarSignals.getRange(`G1:G${solarSignalFormatEnd}`).format.columnWidth = 22;
solarSignals.getRange(`H1:H${solarSignalFormatEnd}`).format.columnWidth = 48;

summary.getRange('A1:H2').merge();
summary.getRange('A1').values = [[data.title]];
summary.getRange('A1:H2').format = {
  fill: navy,
  font: { bold: true, color: '#FFFFFF', size: 20 },
  verticalAlignment: 'center',
  horizontalAlignment: 'left',
};
summary.getRange('A3:H3').merge();
summary.getRange('A3').values = [[data.scope]];
summary.getRange('A3:H3').format = {
  fill: '#EEF2F8',
  font: { bold: true, color: navy, size: 11 },
  verticalAlignment: 'center',
};
summary.getRange('A4:H4').merge();
summary.getRange('A4').values = [[`Generated ${data.generatedAt} | Filtered management export`]];
summary.getRange('A4:H4').format = { font: { color: muted, italic: true, size: 9 } };

const dailyEnd = Math.max(2, dailyRows.length + 1);
const warehouseFilter = data.solarSeparated ? `'Daily Usage'!$J$2:$J$${dailyEnd}` : null;
const cards = [
  { label: data.energyCardLabel, cols: 'A:B', cell: 'A7', formula: data.solarSeparated ? `=SUMIF(${warehouseFilter},"Warehouse consumption",'Daily Usage'!$C$2:$C$${dailyEnd})` : `=SUM('Daily Usage'!$C$2:$C$${dailyEnd})`, format: '#,##0.0 "kWh"' },
  { label: 'HIGHEST LOAD', cols: 'C:D', cell: 'C7', formula: data.solarSeparated ? `=MAXIFS('Daily Usage'!$D$2:$D$${dailyEnd},${warehouseFilter},"Warehouse consumption")` : `=MAX('Daily Usage'!$D$2:$D$${dailyEnd})`, format: '#,##0.0 "kW"' },
  { label: 'HIGHEST DEMAND', cols: 'E:F', cell: 'E7', formula: data.solarSeparated ? `=MAXIFS('Daily Usage'!$E$2:$E$${dailyEnd},${warehouseFilter},"Warehouse consumption")` : `=MAX('Daily Usage'!$E$2:$E$${dailyEnd})`, format: '#,##0.0 "kVA"' },
  { label: 'UNUSUAL READINGS', cols: 'G:H', cell: 'G7', formula: `=COUNTIF('Daily Usage'!$F$2:$F$${dailyEnd},"Yes")`, format: '#,##0' },
];
for (const card of cards) {
  summary.getRange(`${card.cols.split(':')[0]}6:${card.cols.split(':')[1]}6`).merge();
  summary.getRange(`${card.cols.split(':')[0]}6`).values = [[card.label]];
  summary.getRange(`${card.cols.split(':')[0]}7:${card.cols.split(':')[1]}8`).merge();
  summary.getRange(card.cell).formulas = [[card.formula]];
  summary.getRange(card.cell).format.numberFormat = card.format;
  summary.getRange(`${card.cols.split(':')[0]}6:${card.cols.split(':')[1]}8`).format = {
    fill: pale,
    borders: { preset: 'outside', style: 'thin', color: border },
    verticalAlignment: 'center',
    horizontalAlignment: 'center',
  };
  summary.getRange(`${card.cols.split(':')[0]}6`).format.font = { bold: true, color: muted, size: 9 };
  summary.getRange(card.cell).format.font = { bold: true, color: navy, size: 16 };
}

let insightHeaderRow = 10;
if (data.solarSeparated) {
  summary.getRange('A10:B10').merge();
  summary.getRange('C10:D10').merge();
  summary.getRange('E10:H10').merge();
  summary.getRange('A10').values = [['SOLAR GENERATED']];
  summary.getRange('C10').formulas = [[`=SUMIF('Daily Usage'!$J$2:$J$${dailyEnd},"Solar generation",'Daily Usage'!$C$2:$C$${dailyEnd})`]];
  summary.getRange('C10').format.numberFormat = '#,##0.0 "kWh"';
  summary.getRange('E10').values = [['Generated electricity supplied by the solar installation; shown separately from warehouse consumption.']];
  summary.getRange('A10:H10').format = {
    fill: '#FFF8E6',
    font: { color: navy },
    borders: { preset: 'outside', style: 'thin', color: '#F1C36A' },
    verticalAlignment: 'center',
    wrapText: true,
  };
  summary.getRange('A10').format.font = { bold: true, color: navy };
  summary.getRange('C10').format.font = { bold: true, color: navy, size: 13 };
  summary.getRange('A10:H10').format.rowHeight = 34;
  insightHeaderRow = 12;
}
summary.getRange(`A${insightHeaderRow}:H${insightHeaderRow}`).merge();
summary.getRange(`A${insightHeaderRow}`).values = [['WHAT THE FILTERED DATA SHOWS']];
summary.getRange(`A${insightHeaderRow}:H${insightHeaderRow}`).format = { fill: orange, font: { bold: true, color: '#FFFFFF' } };
let insightRow = insightHeaderRow + 1;
for (const insight of data.insights) {
  summary.getRange(`A${insightRow}:B${insightRow}`).merge();
  summary.getRange(`C${insightRow}:H${insightRow}`).merge();
  summary.getRange(`A${insightRow}`).values = [[insight.title]];
  summary.getRange(`C${insightRow}`).values = [[insight.text]];
  summary.getRange(`A${insightRow}:H${insightRow}`).format = {
    fill: insightRow % 2 === 1 ? '#FFFFFF' : pale,
    borders: { preset: 'outside', style: 'thin', color: border },
    verticalAlignment: 'top',
    wrapText: true,
  };
  summary.getRange(`A${insightRow}`).format.font = { bold: true, color: navy };
  summary.getRange(`C${insightRow}`).format.font = { color: navy };
  summary.getRange(`A${insightRow}:H${insightRow}`).format.rowHeight = 34;
  insightRow += 1;
}

const areaHeaderRow = insightRow + 1;
summary.getRange(`A${areaHeaderRow}:D${areaHeaderRow}`).values = [[
  'Area', 'Electricity used (kWh)', 'Share of total', 'Unusual readings',
]];
summary.getRange(`A${areaHeaderRow}:D${areaHeaderRow}`).format = {
  fill: navy,
  font: { bold: true, color: '#FFFFFF' },
};
let areaRow = areaHeaderRow + 1;
for (const area of data.areas) {
  summary.getRange(`A${areaRow}`).values = [[area]];
  summary.getRange(`B${areaRow}`).formulas = [[`=SUMIF('Daily Usage'!$B$2:$B$${dailyEnd},A${areaRow},'Daily Usage'!$C$2:$C$${dailyEnd})`]];
  summary.getRange(`C${areaRow}`).formulas = [[`=IFERROR(B${areaRow}/$A$7,0)`]];
  summary.getRange(`D${areaRow}`).formulas = [[`=COUNTIFS('Daily Usage'!$B$2:$B$${dailyEnd},A${areaRow},'Daily Usage'!$F$2:$F$${dailyEnd},"Yes")`]];
  summary.getRange(`A${areaRow}:D${areaRow}`).format.borders = {
    insideHorizontal: { style: 'thin', color: border },
  };
  areaRow += 1;
}
if (areaRow > areaHeaderRow + 1) {
  summary.getRange(`B${areaHeaderRow + 1}:B${areaRow - 1}`).format.numberFormat = '#,##0.0';
  summary.getRange(`C${areaHeaderRow + 1}:C${areaRow - 1}`).format.numberFormat = '0.0%';
  summary.getRange(`D${areaHeaderRow + 1}:D${areaRow - 1}`).format.numberFormat = '#,##0';
}

const summaryFormatEnd = Math.max(areaRow, 20);
summary.getRange(`A1:H${summaryFormatEnd}`).format.columnWidth = 16;
summary.getRange(`A1:B${summaryFormatEnd}`).format.columnWidth = 19;
summary.getRange(`C1:H${summaryFormatEnd}`).format.columnWidth = 16;
summary.freezePanes.freezeRows(4);

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

if (previewPath) {
  const parsed = path.parse(previewPath);
  const previewBase = path.join(parsed.dir, parsed.name);
  const previews = [
    ['Summary', `A1:H${summaryFormatEnd}`, `${previewBase}_summary.png`],
    ['Daily Usage', `A1:J${Math.min(dailyFormatEnd, 31)}`, `${previewBase}_daily_usage.png`],
    ['Unusual Usage', `A1:H${Math.min(unusualFormatEnd, 31)}`, `${previewBase}_unusual_usage.png`],
    ['Solar Signals', `A1:H${Math.min(solarSignalFormatEnd, 31)}`, `${previewBase}_solar_signals.png`],
  ];
  for (const [sheetName, range, target] of previews) {
    const preview = await workbook.render({ sheetName, range, scale: 1.3, format: 'png' });
    await fs.writeFile(target, new Uint8Array(await preview.arrayBuffer()));
  }
  const check = await workbook.inspect({
    kind: 'table',
    range: `Summary!A1:H${summaryFormatEnd}`,
    include: 'values,formulas',
    tableMaxRows: 30,
    tableMaxCols: 8,
  });
  const errors = await workbook.inspect({
    kind: 'match',
    searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A',
    options: { useRegex: true, maxResults: 100 },
    summary: 'formula error scan',
  });
  process.stdout.write(`${check.ndjson}\n${errors.ndjson}\n`);
}
