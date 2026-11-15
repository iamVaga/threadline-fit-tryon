// Generate the same scores used in the Results tab from the deployed engine.
import fs from 'node:fs';
import {recommend} from './static/fit/engine.mjs';

const [, , casePath = '.local/fit_cases.json', modelPath = 'static/fit/data.json',
  resultPath = 'static/fit/results.json', predictionPath = '.local/fit_predictions.csv'] = process.argv;
const read = path => JSON.parse(fs.readFileSync(path, 'utf8'));
const rows = read(casePath);
const model = read(modelPath);
const parts = new Map();
const examples = {};
const predictions = ['test_id,sku,predicted,offered,exact,within_acceptable,label_source'];
function add(key, row, prediction) {
  if (!parts.has(key)) parts.set(key, {n: 0, exact: 0, within: 0, first: 0, usual: 0, usualN: 0});
  const scores = parts.get(key);
  scores.n++;
  scores.exact += row.correct === prediction.size;
  scores.within += row.acceptable.includes(prediction.size);
  scores.first += row.acceptable.includes(row.first);
  if (row.usual) {
    scores.usualN++;
    scores.usual += row.acceptable.includes(row.usual);
  }
}
for (const row of rows) {
  const item = model.products[row.sku];
  const prediction = recommend(model, row.sku, row);
  if (!item.sizes.includes(prediction.size)) throw new Error(`Offered sizes violation in ${row.id}`);
  add('All cases', row, prediction);
  add(row.source, row, prediction);
  add(item.category, row, prediction);
  if (!row.history.length) add('No kept or exchanged history in this size category', row, prediction);
  if (item.category === 'Women · Kurtas' && row.source === 'Kept after exchange') add('Women · Kurtas after exchange', row, prediction);
  if (model.examples.some(example => example.id === row.id)) examples[row.id] = prediction.size;
  predictions.push([row.id, row.sku, prediction.size, 'yes', Number(row.correct === prediction.size),
    Number(row.acceptable.includes(prediction.size)), row.source].join(','));
}
const results = {version: model.version, slices: Object.fromEntries(parts), examples};
fs.writeFileSync(resultPath, JSON.stringify(results, null, 2) + '\n');
fs.writeFileSync(predictionPath, predictions.join('\n') + '\n');
console.log(JSON.stringify(results, null, 2));
