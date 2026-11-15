// The shop and the October evaluation both call recommend().
const letters = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL'];
const numeric = {
  'Women · Jeans': ['26', '28', '30', '32', '34', '36'],
  'Men · Jeans': ['28', '30', '32', '34', '36', '38', '40'],
};
const family = category => numeric[category] ? category : category.split(' · ')[0];
const scale = category => numeric[category] || letters;
const weight = (weights, key) => weights[key] || 0;

function catalogueEffect(weights, item) {
  const {category, brand, fit, stretch} = item;
  return weight(weights, `category|${category}`) + weight(weights, `brand|${category}|${brand}`) +
    weight(weights, `fit|${category}|${fit}`) + (stretch ? weight(weights, `stretch|${category}`) : 0);
}

function profileEstimate(weights, basis, sizes, options) {
  let estimate = weights.intercept;
  const index = sizes.indexOf(options.usual);
  if (index >= 0) estimate += weight(weights, 'usual') * (index - 2.5) + weight(weights, 'hasUsual');
  for (const [key, [center, step]] of Object.entries(basis)) {
    const value = options.measurements?.[key];
    if (Number.isFinite(value)) estimate += weight(weights, key) * (value - center) / step + weight(weights, `has_${key}`);
  }
  return estimate;
}

export function recommend(model, skuOrProduct, options = {}) {
  const item = typeof skuOrProduct === 'string' ? model.products[skuOrProduct] : skuOrProduct;
  const {category, brand} = item;
  const group = family(category);
  const sizes = scale(category);
  const {weights, measureBasis} = model.groups[group];
  const profile = profileEstimate(weights, measureBasis, sizes, options);
  const anchors = [];
  for (const order of options.history || []) {
    const product = model.products[order.sku] ?? order.product;
    const index = sizes.indexOf(order.size);
    if (!product || family(product.category) !== group || index < 0) continue;
    anchors.push({
      value: index - catalogueEffect(weights, product) + (order.adjustment || 0),
      brand: product.brand,
      size: order.size,
      weight: product.category === category ? 1 : 0.7,
      fit: order.fit || 'kept',
    });
  }
  const totalWeight = anchors.reduce((total, anchor) => total + anchor.weight, 0);
  const profileWeight = 1.5;
  const score = (profile * profileWeight + anchors.reduce((sum, a) => sum + a.value * a.weight, 0)) /
    (profileWeight + totalWeight) + catalogueEffect(weights, item) + (options.preference || 0);
  const rigid = Boolean(numeric[category]) && item.stretch === 0;
  const grade = sizes.map((size, index) => ({size, index, distance: Math.abs(index - score)}))
    .filter(candidate => item.sizes.includes(candidate.size))
    .sort((a, b) => a.distance - b.distance || a.index - b.index);
  let [chosen, alternative] = grade;
  const sizeUp = rigid && alternative && alternative.index > chosen.index &&
    alternative.distance - chosen.distance <= 0.2;
  if (sizeUp) [chosen, alternative] = [alternative, chosen];

  const main = numeric[category] ? 'waist' : 'bust';
  const hasMeasure = Number.isFinite(options.measurements?.[main]);
  const hasUsual = sizes.includes(options.usual);
  const available = ['hip', 'height', 'weight'].filter(key => Number.isFinite(options.measurements?.[key]));
  const anchor = anchors.findLast(a => a.fit === 'kept') ?? anchors.at(-1);
  const source = anchor ? `${anchor.size} in ${anchor.brand} (${anchor.fit})` :
    hasMeasure ? `your ${main} measurement` : hasUsual ? `your usual size ${options.usual}` :
    available.length ? `your ${available.join(' and ')}` : 'common sizes for this category';
  const direction = model.returnDirection[`${category}|${brand}`] || 0;
  const brandEvidence = Math.abs(direction) >= 0.08
    ? ` ${brand} has more returns marked too ${direction > 0 ? 'small' : 'large'} in this category.` : '';
  const reason = `Based on ${source} and the sizes offered for this garment.` +
    (sizeUp ? ' We chose the larger of two close sizes because this denim has no stretch.' : brandEvidence);
  return {
    size: chosen.size,
    alternative: alternative && alternative.distance - chosen.distance < 0.4 ? alternative.size : null,
    evidence: anchors.filter(a => a.fit === 'kept').length >= 2 || (anchor && hasMeasure) ? 'More evidence' :
      anchor || hasMeasure ? 'Some evidence' : 'Limited evidence',
    reason,
  };
}
