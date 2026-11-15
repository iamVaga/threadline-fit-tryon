const $ = id => document.getElementById(id);
const visible = (id, show) => $(id).classList.toggle('hid', !show);
const letterSizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL'];
const jeansSizes = ['24', '26', '28', '30', '32', '34', '36', '38', '40', '42'];
const pageSize = 12;
const mainTabs = [...document.querySelectorAll('nav [data-t]')];
const featuredImages = ['in-1167a4f332.jpg', 'in-b5e04df86a.jpg', 'fs-15.png', 'in-25b7a65354.jpg'];
const cur = { p: null, c: 0 };
let brands = [];
let catalogue = [];
let fitProducts = [];
let fitCatalogueRequest;
let catFilter = '';
let visibleProducts = pageSize;
let lastCardId;
let activeView = 'shop';
let recommendRequest = 0;

function showView(view) {
  if (activeView !== view) stopCamera();
  activeView = view;
  const navView = view === 'workspace' ? (cur.p ? 'shop' : 'size') : view;
  mainTabs.forEach(tab => {
    tab.classList.toggle('on', tab.dataset.t === navView);
    tab.setAttribute('aria-pressed', String(tab.dataset.t === navView));
  });
  for (const section of ['shop', 'workspace', 'fit', 'res']) visible(section, view === section);
}

function focusSection(element) {
  element.focus({ preventScroll: true });
  element.scrollIntoView({ block: 'start' });
}

function openCollection() {
  showView('shop');
  const card = [...$('catalog').children].find(item => item.dataset.id === lastCardId);
  focusSection(card || $('collectionTitle'));
}

function showPanel(panel, focus = true) {
  if (panel !== 'try') stopCamera();
  visible('sizeStep', panel === 'size');
  visible('tryStep', panel === 'try');
  for (const [id, name] of [['showSize', 'size'], ['showTryOn', 'try']]) {
    $(id).setAttribute('aria-expanded', String(panel === name));
  }
  if (focus && panel) focusSection($(panel === 'size' ? 'sizeStep' : 'tryStep'));
}

function resetSizeForm(sku = '') {
  recommendRequest++;
  $('skuLookup').value = sku;
  $('recommendStatus').textContent = '';
  $('technicalTiming').textContent = '';
  visible('out', false);
  visible('fitChoice', false);
  updateUsualSizes();
}

function openSizeFinder() {
  cur.p = null;
  resetSizeForm();
  visible('pickedImage', false);
  visible('productMeta', false);
  $('workspace').classList.add('standalone');
  $('sizeIntro').textContent = 'Choose a garment to get a size recommendation.';
  visible('fitPicker', true);
  $('fitSearch').value = '';
  $('fitCategory').value = '';
  renderFitMatches();
  loadFitProducts();
  $('testDetails').hidden = false;
  $('trySampleCase').hidden = false;
  $('testDetails').open = false;
  $('skuField').open = false;
  visible('skuField', true);
  showView('workspace');
  showPanel('size');
}

mainTabs.forEach(button => {
  button.onclick = () => {
    if (button.dataset.t === 'size') {
      openSizeFinder();
    } else {
      showView(button.dataset.t);
      window.scrollTo(0, 0);
    }
  };
});
$('backToShop').onclick = openCollection;
$('showTryOn').onclick = () => showPanel('try');
$('showSize').onclick = () => {
  if (cur.p?.free) {
    openSizeFinder();
    runSampleCase();
  } else {
    showPanel('size');
  }
};

const priceFormat = (currency = 'INR') => new Intl.NumberFormat(currency === 'USD' ? 'en-US' : 'en-IN', {
  style: 'currency', currency, maximumFractionDigits: 2, minimumFractionDigits: 0,
});
function money(amount, currency) {
  return amount == null ? '' : priceFormat(currency).format(Number(amount));
}

function productPhoto(path, alt = '') {
  const image = document.createElement('img');
  image.src = path;
  image.alt = alt;
  image.decoding = 'async';
  return image;
}

function renderCatalog() {
  const q = $('cs').value.trim().toLowerCase(), br = $('cbrand').value, so = $('csort').value;
  const list = catalogue.filter(p => (!br || p.brand === br) && (!catFilter || p.cat === catFilter) &&
    (!q || [p.name, p.brand, p.cat, p.id, ...p.cols.map(col => col.name)].join(' ').toLowerCase().includes(q)));
  if (so) list.sort((a, b) => {
    if (a.price == null) return b.price == null ? 0 : 1;
    if (b.price == null) return -1;
    return a.currency.localeCompare(b.currency) || (so === 'lo' ? a.price - b.price : b.price - a.price);
  });
  $('catalog').replaceChildren(...list.slice(0, visibleProducts).map((p, index) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'cat-card';
    card.dataset.id = p.id;
    const media = document.createElement('span');
    media.className = 'ct';
    const image = productPhoto(p.cols[0].image);
    if (index >= 4) image.loading = 'lazy';
    media.append(image);
    const name = document.createElement('span');
    name.className = 'nm';
    name.textContent = p.name;
    const brand = document.createElement('span');
    brand.className = 'br';
    brand.textContent = p.brand === 'Indo Fashion' ? p.cat : p.brand;
    const price = document.createElement('span');
    price.className = 'pr';
    price.textContent = money(p.price, p.currency);
    card.append(media, brand, name);
    if (price.textContent) card.append(price);
    card.onclick = () => selectProduct(p);
    return card;
  }));
  $('catNote').textContent = list.length === 1 ? '1 garment' : list.length + ' garments';
  visible('catalogMore', list.length > visibleProducts);
  visible('catEmpty', list.length === 0);
}

function renderPicked() {
  const { p, c } = cur;
  const v = p.cols[c];
  $('swSvg').replaceChildren(productPhoto(v.image, p.name));
  $('swName').textContent = p.cols.length > 1 ? v.name : '';
  $('pn').textContent = p.name;
  $('productBrand').textContent = p.brand;
  $('pmeta').textContent = [p.cat, money(p.price, p.currency)].filter(Boolean).join(' · ');
  visible('productColours', p.cols.length > 1);
  const topPhoto = p.gcat === 'tops';
  const crop = topPhoto ? 'Keep your head to hips in frame.' : 'Keep your head to feet in frame.';
  $('photoGuide').textContent = `Use a clear photo of one person. ${crop}`;
  $('cameraGuide').textContent = `${crop} Use even lighting and a plain background.`;
  $('colourChips').replaceChildren(...p.cols.map(({name, hex}, index) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.style.setProperty('--chip', hex);
    chip.setAttribute('aria-label', name);
    chip.setAttribute('aria-pressed', String(index === c));
    chip.title = name;
    chip.onclick = () => {
      cur.c = index;
      $('gf').value = '';
      resetSizeForm(cur.p.free ? '' : cur.p.cols[index].sku);
      renderPicked();
      $('colourChips').children[index].focus();
      clearPreview();
      sync();
    };
    return chip;
  }));
}

function selectProduct(p) {
  cur.p = p;
  cur.c = 0;
  lastCardId = p.id;
  resetSizeForm(p.free ? '' : p.cols[0].sku);
  $('gf').value = '';
  $('msg').textContent = '';
  $('garmentDetails').open = false;
  $('garmentDetails').querySelector('summary').textContent = 'Use a different garment photo';
  clearPreview();
  renderPicked();
  visible('pickedImage', true);
  visible('productMeta', true);
  visible('sampleFitNotice', p.free);
  visible('skuField', false);
  visible('fitPicker', false);
  $('testDetails').hidden = true;
  $('trySampleCase').hidden = true;
  $('sizeIntro').textContent = 'Tell us what size you usually wear and how you like it to fit.';
  $('showSize').textContent = p.free ? 'Try a size demo' : 'Find my size';
  $('workspace').classList.remove('standalone');
  showView('workspace');
  showPanel(null);
  focusSection($('pn'));
  sync();
}

function setCategory(category = '') {
  catFilter = category;
  visibleProducts = pageSize;
  $('catChips').querySelectorAll('button').forEach(chip => {
    chip.setAttribute('aria-pressed', String(chip.dataset.category === category));
  });
  renderCatalog();
}

function loadFitProducts() {
  if (fitCatalogueRequest) return;
  $('fitSearchState').textContent = 'Loading size catalogue…';
  fitCatalogueRequest = fetch('/api/fit-products').then(response => {
    if (!response.ok) throw new Error();
    return response.json();
  }).then(items => {
    fitProducts = items;
    const categories = [...new Set(items.map(item => item.category))].sort();
    $('fitCategory').append(...categories.map(category => new Option(category)));
    renderFitMatches();
    updateUsualSizes();
  }).catch(() => {
    fitCatalogueRequest = null;
    $('fitSearchState').textContent = 'The size catalogue did not load. You can still enter a product SKU or try the demo.';
  });
}

function renderFitMatches() {
  const category = $('fitCategory').value;
  const terms = $('fitSearch').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
  $('fitMatches').replaceChildren();
  if (!fitProducts.length) return;
  $('fitSearchState').textContent = '';
  if (!category && !terms.length) return;
  const matched = fitProducts.filter(item => {
    const text = [item.name, item.brand, item.sku, item.colour].join(' ').toLowerCase();
    return (!category || item.category === category) && terms.every(term => text.includes(term));
  });
  $('fitSearchState').textContent = !matched.length ? 'No matching garments.'
    : matched.length > 6 ? 'Showing 6 of ' + matched.length + ' matches. Narrow your search to see more.' : '';
  for (const item of matched.slice(0, 6)) {
    const row = document.createElement('li');
    const button = document.createElement('button');
    const label = document.createElement('span');
    const brand = document.createElement('small');
    button.type = 'button';
    label.textContent = item.name;
    brand.textContent = item.brand + ' · ' + item.sku;
    button.append(label, brand);
    button.onclick = () => {
      resetSizeForm(item.sku);
      $('fitChoice').textContent = item.brand + ' · ' + item.name;
      visible('fitChoice', true);
      $('fitSearchState').textContent = '';
      $('fitMatches').replaceChildren();
      $('us').focus();
    };
    row.append(button);
    $('fitMatches').append(row);
  }
}
$('fitCategory').onchange = renderFitMatches;
$('fitSearch').oninput = renderFitMatches;

function updateUsualSizes() {
  const previous = $('us').value;
  const sku = $('skuLookup').value.trim().toUpperCase();
  const category = fitProducts.find(item => item.sku === sku)?.category;
  const sizes = category ? (/jeans/i.test(category) ? jeansSizes : letterSizes) : [...letterSizes, ...jeansSizes];
  $('us').replaceChildren(new Option('Not sure', ''), ...sizes.map(size => new Option(size)));
  if (sizes.includes(previous)) $('us').value = previous;
}

for (const id of ['cs', 'cbrand', 'csort']) $(id).oninput = () => {
  visibleProducts = pageSize;
  renderCatalog();
};
$('skuLookup').oninput = () => {
  visible('fitChoice', false);
  visible('out', false);
  recommendRequest++;
  updateUsualSizes();
};
$('catalogMore').onclick = () => {
  const firstNewIndex = visibleProducts;
  visibleProducts += pageSize;
  renderCatalog();
  $('catalog').children[firstNewIndex]?.focus();
};
$('clearFilters').onclick = () => {
  for (const field of ['cs', 'cbrand', 'csort']) $(field).value = '';
  setCategory();
  $('cs').focus();
};
$('catalogFilters').addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    $('catalogFilters').open = false;
    $('catalogFilters').querySelector('summary').focus();
  }
});
document.addEventListener('click', event => {
  if (!$('catalogFilters').contains(event.target)) $('catalogFilters').open = false;
});

fetch('/api/catalog').then(response => {
  if (!response.ok) throw new Error();
  return response.json();
}).then(items => {
  catalogue = items.filter(item => item.variants.some(variant => variant.image)).map(item => ({
    id: item.sku_id, brand: item.brand, name: item.name.trim(), price: item.price, currency: item.currency || 'INR',
    cat: item.category_label || 'Kurtas', gcat: item.garment_category, free: !!item.free,
    cols: item.variants.filter(v => v.image).map(v => ({ name: v.colour, hex: v.hex, image: v.image, sku: v.sku })),
  }));
  const rank = item => {
    const index = featuredImages.indexOf(item.cols[0].image.split('/').pop());
    return index === -1 ? featuredImages.length : index;
  };
  catalogue.sort((a, b) => rank(a) - rank(b));
  const available = [...new Set(catalogue.map(item => item.brand))].sort();
  $('cbrand').replaceChildren(new Option('All brands', ''), ...available.map(brand => new Option(brand)));
  const cats = [...new Set(catalogue.map(item => item.cat))].sort();
  $('catChips').replaceChildren(...['', ...cats].map(category => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.textContent = category || 'All';
    chip.dataset.category = category;
    chip.onclick = () => setCategory(category);
    return chip;
  }));
  setCategory();
  updateUsualSizes();
}).catch(() => {
  $('catNote').textContent = 'The collection is unavailable. You can still use the size finder.';
});

function runSampleCase() {
  $('testLookup').value = 'FT-10026';
  $('testDetails').open = true;
  rec(true);
}
$('heroDemo').onclick = () => { openSizeFinder(); runSampleCase(); };
$('trySampleCase').onclick = runSampleCase;
$('testLookup').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); rec(true); }
});
updateUsualSizes();
async function rec(lookup = false) {
  const serial = ++recommendRequest;
  const started = performance.now();
  const data = {};
  if (lookup) {
    data.test_id = $('testLookup').value.trim().toUpperCase();
    visible('fitChoice', false);
  }
  else {
    data.sku_id = $('skuLookup').value.trim().toUpperCase();
    data.customer_id = $('customerLookup').value.trim().toUpperCase() || null;
    data.usual_size = $('us').value || null;
    data.usual_brand = $('ub').value || null;
    data.fit_preference = Number($('fp').value);
    data.consent_measurements = $('c2').checked;
    if (data.consent_measurements) {
      for (const [id, key] of [['bu','bust'], ['wa','waist'], ['hi','hip']]) {
        if ($(id).value) data[key] = Number($(id).value);
      }
    }
  }
  if (lookup && !data.test_id) {
    $('recommendStatus').textContent = 'Enter a test ID, for example FT-10022.';
    return;
  }
  if (!lookup && !data.sku_id) {
    $('recommendStatus').textContent = 'Choose a garment or try the size demo.';
    return;
  }
  visible('out', false);
  $('recommendStatus').textContent = 'Finding your size…';
  try {
    const response = await fetch('/api/recommend', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    const result = await response.json();
    if (serial !== recommendRequest) return;
    if (!response.ok) {
      $('recommendStatus').textContent = result.error || 'Check your entries and try again.';
      return;
    }
    $('sz').textContent = result.size;
    $('cf').textContent = result.confidence === null ? 'Uncalibrated' : `${(result.confidence*100).toFixed(1)}% confidence`;
    $('rs').textContent = result.reason;
    visible('confidenceDetails', result.confidence !== null);
    $('prec').textContent = result.confidence === null ? '' : `Share of matching recommendations in a late September group with similar fit information (${result.calibration_n.toLocaleString()} cases).`;
    $('sizeHint').textContent = 'Between available sizes';
    $('sizeHint').classList.toggle('hid', !result.between_sizes);
    $('cmp').textContent = `${result.brand} · ${result.category} · ${result.sku_id}`;
    $('notes').textContent = result.chart_unreliable ? 'Published chart flagged as unreliable in Fit & Returns.' : '';
    const serverMs = response.headers.get('Server-Timing')?.match(/recommend;dur=([\d.]+)/)?.[1];
    const elapsed = Math.round(performance.now()-started);
    $('technicalTiming').textContent = serverMs
      ? `${elapsed} ms total · ${Number(serverMs).toFixed(2)} ms server time.`
      : `${elapsed} ms total.`;
    $('recommendStatus').textContent = '';
    visible('out', true);
    focusSection($('out'));
    if (lookup) {
      $('skuLookup').value = result.sku_id;
      updateUsualSizes();
    }
    if (lookup && result.needs_input) {
      $('recommendStatus').textContent = 'Add your usual size and fit preference to refine this recommendation.';
    }
  } catch {
    if (serial === recommendRequest) $('recommendStatus').textContent = 'Could not reach the fit service. Try again.';
  }
}
$('go').onclick = () => rec();
$('lookupCase').onclick = () => rec(true);
// consent + photo
let SID = crypto.randomUUID();
let pendingRender = null, deleting = false, generation = 0, expiry = 0, photoExpiry = 0;
let photoConfigLoaded = false, remoteBackend = null;
let toastTimer, progressTimer;
let originalUrl;
const stages = ['Checking your photo…', 'Preparing your preview…'];

function toast(message) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').classList.remove('hid');
  toastTimer = setTimeout(() => $('toast').classList.add('hid'), 5000);
}
function renderProgress(active) {
  clearInterval(progressTimer);
  $('renderProgress').classList.toggle('hid', !active);
  $('rn').setAttribute('aria-busy', String(active));
  if (!active) return;
  let step = 0;
  $('renderStage').textContent = stages[step];
  progressTimer = setInterval(() => {
    step = Math.min(step + 1, stages.length - 1);
    $('renderStage').textContent = stages[step];
  }, 4500);
}
function resetComparison() {
  if (originalUrl) URL.revokeObjectURL(originalUrl);
  originalUrl = null;
  $('originalLayer').removeAttribute('src');
  $('originalLayer').classList.add('hid');
  $('compareControls').classList.add('hid');
}
$('compareRange').oninput = () => {
  const value = $('compareRange').value;
  $('previewImage').style.setProperty('--compare', value + '%');
  $('compareRange').setAttribute('aria-valuetext', value + '% original photo');
};
function showComparison(file, requestGeneration) {
  const original = $('originalLayer');
  originalUrl = URL.createObjectURL(file);
  original.src = originalUrl;
  original.decode().then(() => {
    if (generation !== requestGeneration) return;
    const canvas = $('cv');
    const originalHeight = canvas.width * original.naturalHeight / original.naturalWidth;
    original.style.height = Math.min(100, originalHeight / canvas.height * 100) + '%';
    $('compareRange').value = 50;
    $('compareRange').oninput();
    original.classList.remove('hid');
    $('compareControls').classList.remove('hid');
  }).catch(() => {
    if (generation === requestGeneration) resetComparison();
  });
}
let cameraStream;
let cameraRequest = 0;
function stopCamera() {
  cameraRequest++;
  if (cameraStream) cameraStream.getTracks().forEach(track => track.stop());
  cameraStream = null;
  $('cameraVideo').srcObject = null;
  $('cameraPanel').classList.add('hid');
}
$('cameraStart').onclick = async () => {
  if ($('cameraStart').disabled) return;
  stopCamera();
  const opening = cameraRequest;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1080},height:{ideal:1920}},audio:false});
    if (opening !== cameraRequest || $('cameraStart').disabled) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    cameraStream = stream;
    $('cameraVideo').srcObject = stream;
    $('cameraPanel').classList.remove('hid');
  } catch {
    $('msg').textContent = 'Camera access is unavailable. You can upload a photo instead.';
  }
};
$('cameraClose').onclick = stopCamera;
window.addEventListener('pagehide', stopCamera);
$('capture').onclick = () => {
  const video = $('cameraVideo');
  if (!cameraStream || !video.videoWidth) return;
  const captureRequest = cameraRequest;
  const frame = document.createElement('canvas');
  frame.width = video.videoWidth;
  frame.height = video.videoHeight;
  frame.getContext('2d').drawImage(video,0,0);
  frame.toBlob(blob => {
    if (!blob || captureRequest !== cameraRequest || $('cameraStart').disabled) return;
    const files = new DataTransfer();
    files.items.add(new File([blob], 'capture.jpg', {type:'image/jpeg'}));
    $('ph').files = files.files;
    $('ph').dispatchEvent(new Event('change',{bubbles:true}));
    stopCamera();
  },'image/jpeg',.9);
};
async function checkClientPhoto(file) {
  if (file.size > 12 * 1024 * 1024) return 'Choose a photo under 12 MB.';
  try {
    const image = await createImageBitmap(file);
    try {
      if (Math.min(image.width,image.height) < 480) return 'Retake at a higher resolution (at least 480 pixels on the short side).';
      if (image.height < image.width) return 'Use a portrait photo. ' + $('photoGuide').textContent;
      const frame = document.createElement('canvas');
      frame.width = frame.height = 40;
      const ctx = frame.getContext('2d');
      ctx.drawImage(image,0,0,40,40);
      const rgba = ctx.getImageData(0,0,40,40).data;
      const light = [];
      for (let i=0; i<rgba.length; i+=4) light.push(.299*rgba[i] + .587*rgba[i+1] + .114*rgba[i+2]);
      light.sort((a,b) => a-b);
      if (light[1200] < 48 || light[1440] - light[160] < 16) return 'Retake in even light from the front.';
    } finally { image.close(); }
  } catch {
    return 'Choose a readable JPG, PNG or WebP photo.';
  }
  return '';
}
fetch('/api/gallery').then(response => {
  if (!response.ok) throw new Error();
  return response.json();
}).then(entries => {
  visible('sampleGallery', entries.length > 0);
  $('galleryStatus').textContent = '';
  for (const entry of entries) {
    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.src = entry.image;
    img.alt = entry.label;
    img.loading = 'lazy';
    const label = document.createElement('figcaption');
    label.textContent = entry.case_id + ' · ' + entry.label;
    figure.append(img,label);
    $('galleryItems').append(figure);
  }
}).catch(() => { $('galleryStatus').textContent = 'The sample gallery could not load.'; });
const day = 24 * 60 * 60 * 1000;
const adult = () => Number.isInteger(Number($('ag').value)) && Number($('ag').value) >= 18;
let thumbFile = null, thumbUrl = null;

function sync() {
  const file = $('ph').files[0] || null;
  const photoConsented = photoConfigLoaded && adult() && $('c1').checked && !deleting;
  if ((photoConsented ? file : null) !== thumbFile) {
    if (thumbUrl) URL.revokeObjectURL(thumbUrl);
    thumbFile = photoConsented ? file : null;
    thumbUrl = thumbFile ? URL.createObjectURL(thumbFile) : null;
    if (thumbUrl) $('phThumb').src = thumbUrl; else $('phThumb').removeAttribute('src');
    $('phThumb').classList.toggle('hid', !thumbFile);
  }
  const need = [];
  if (!photoConfigLoaded) need.push('Loading photo consent…');
  else if (!$('c1').checked || !adult()) need.push('Agree to photo processing and enter an age of 18 or older.');
  else if (!file) need.push('Choose a photo or use your camera.');
  else if (!$('gf').files[0] && !cur.p?.cols[cur.c]?.image) need.push('Add a garment photo to continue.');
  $('ph').disabled = deleting;
  $('cameraStart').disabled = !photoConfigLoaded || !adult() || !$('c1').checked || deleting;
  if ($('cameraStart').disabled) stopCamera();
  $('tryNeeds').textContent = need.join(' ');
  $('rn').disabled = need.length > 0 || !!pendingRender || deleting;
  $('del').disabled = deleting;
  $('clearTryData').disabled = deleting;
  visible('clearTryData', !!file || !!$('ag').value || $('c1').checked || deleting);

}
function clearPreview() {
  generation += 1;
  clearTimeout(expiry);
  resetComparison();
  $('preview').classList.add('hid');
  $('cv').width = 0;
  for (const id of ['cv', 'lb', 'note']) $(id).classList.add('hid');
}
async function erasePhoto() {
  if (deleting) return;
  deleting = true;
  clearTimeout(photoExpiry);
  clearPreview();
  $('ph').value = '';
  sync();
  try {
    stopCamera();
    if (pendingRender) await pendingRender;
    const body = new FormData();
    body.append('session', SID);
    const response = await fetch('/api/delete', {method: 'POST', body});
    if (!response.ok || !(await response.json()).deleted) throw new Error();
    SID = crypto.randomUUID();
    $('msg').textContent = 'FitCheck deleted its photo and preview copies and cleared any measurements you chose to delete from this tab.';
    toast($('msg').textContent);
  } catch {
    $('msg').textContent = 'The local preview was cleared. Server deletion could not be confirmed. Please try again.';
    toast($('msg').textContent);
  } finally {
    deleting = false;
    sync();
  }
}
$('ag').oninput = sync;
$('ag').onchange = () => {
  if ($('ag').value && !adult() && $('ph').files[0]) erasePhoto();
  sync();
};
$('c1').onchange = () => {
  if (!$('c1').checked) erasePhoto();
  sync();
};
const BODY = ['bu', 'wa', 'hi'];
$('c2').onchange = () => {
  if (!$('c2').checked) {
    recommendRequest++;
    $('recommendStatus').textContent = '';
    BODY.forEach(id => { $(id).value = ''; });
    $('out').classList.add('hid');

  }
  BODY.forEach(id => { $(id).disabled = !$('c2').checked; });
};
$('c2').onchange();
for (const id of ['ph', 'gf']) {
  $(id).onchange = () => {
    if (id === 'ph') {
      clearTimeout(photoExpiry);
      if ($('ph').files[0]) photoExpiry = setTimeout(erasePhoto, day);
    }
    clearPreview();
    $('msg').textContent = '';
    sync();
  };
}
const drop = $('photoDrop');
drop.ondragover = event => {
  event.preventDefault();
  if (!$('ph').disabled) drop.dataset.dragging = '';
};
drop.ondragleave = () => { delete drop.dataset.dragging; };
drop.ondrop = event => {
  event.preventDefault();
  delete drop.dataset.dragging;
  if ($('ph').disabled || !event.dataTransfer.files.length) return;
  const file = event.dataTransfer.files[0];
  if (!['image/jpeg','image/png','image/webp'].includes(file.type)) {
    $('msg').textContent = 'Choose a JPG, PNG or WebP photo.';
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  if (event.target === $('phThumb') && $('ph').files[0]) {
    $('gf').files = transfer.files;
    $('gf').dispatchEvent(new Event('change', {bubbles: true}));
    $('garmentDetails').open = true;
    return;
  }
  $('ph').files = transfer.files;
  $('ph').dispatchEvent(new Event('change', {bubbles: true}));
};

fetch('/api/tryon-config').then(response => {
  if (!response.ok) throw new Error();
  return response.json();
}).then(config => {
  remoteBackend = config.remote_backend;
  if (remoteBackend) {
    const provider = {local_http: 'the team GPU server', hf: 'Hugging Face', replicate: 'Replicate', gemini: 'Google Gemini'}[remoteBackend];
    $('photoPrivacy').textContent = `I am a consenting adult team member. FitCheck sends my photo to ${provider}, a third-party model host, to create the preview. FitCheck deletes its copies on request or within 24 hours. The host may retain or log the photo.`;
    if (remoteBackend === 'gemini') $('photoPrivacy').textContent += ' Gemini is experimental and excluded from the scored evaluation. Use only a service configuration that prohibits training on inputs.';
    if (remoteBackend === 'hf') $('photoPrivacy').textContent += ' The public Space can queue or hit its quota.';
  }
  photoConfigLoaded = true;
  $('c1').disabled = false;
  sync();
}).catch(() => {
  $('msg').textContent = 'Photo preview is temporarily unavailable. Reload the page to try again.';
});
$('rn').onclick = async () => {
  if ($('rn').disabled) return;
  clearPreview();
  const requestGeneration = generation;
  const body = new FormData();
  body.append('session', SID);
  body.append('age', String(Number($('ag').value)));
  body.append('consent_photo', 'true');
  if (remoteBackend) {
    body.append('consent_remote', remoteBackend);
  }
  body.append('garment_category', cur.p?.gcat || 'one-pieces');
  body.append('desc', cur.p ? cur.p.cols[cur.c].name + ' ' + cur.p.name : 'kurta');
  const photo = $('ph').files[0];
  body.append('photo', photo);
  const garmentFile = $('gf').files[0];
  if (garmentFile) body.append('garment', garmentFile);
  else if (cur.p?.cols[cur.c]?.image) body.append('sku_id',cur.p.id);
  else return;
  $('rn').disabled = true;
  const validation = await checkClientPhoto(photo);
  if (requestGeneration !== generation || deleting || !$('c1').checked || !adult()) { sync(); return; }
  if (validation) { $('msg').textContent = validation; sync(); return; }
  $('msg').textContent = 'Checking the photo and preparing the preview…';
  renderProgress(true);
  pendingRender = (async () => {
    try {
      const response = await fetch('/api/tryon', {method: 'POST', body});
      const result = await response.json();
      if (requestGeneration !== generation) return;
      if (!response.ok) {
        $('msg').textContent = result.error || 'Could not prepare the preview.';
        if (response.status >= 500) $('sampleGallery').open = true;
        return;
      }
      const image = new Image();
      image.src = result.image;
      await image.decode();
      if (requestGeneration !== generation) return;
      const canvas = $('cv');
      canvas.width = image.width;
      canvas.height = image.height;
      canvas.getContext('2d').drawImage(image, 0, 0);
      for (const id of ['cv', 'lb', 'note']) $(id).classList.remove('hid');
      $('preview').classList.remove('hid');
      if (!result.mock) showComparison(photo, requestGeneration);
      const reason = {
        mock_mode: 'Demo mode is active.',
        model_unavailable: 'The try-on model is unavailable.',
        quota_exceeded: 'The try-on provider says the usage quota is exhausted. Enable billing or wait for the quota to reset.',
        preservation_check: 'The preview did not pass the clothing-only check.'
      }[result.placeholder_reason];
      $('msg').textContent = result.mock
        ? `${reason || 'Try-on is unavailable.'} PLACEHOLDER · original photo. Preview ${result.renders_used} of 3.`
        : `Done in ${result.latency_s}s. Preview ${result.renders_used} of 3.` + ((result.adjusted || []).length ? ' We straightened your photo for fitting, then placed the result back on your original.' : '');
      expiry = setTimeout(() => {
        clearPreview();
        $('ph').value = '';
        sync();
      }, day);
    } catch {
      if (requestGeneration === generation) {
        $('msg').textContent = 'Could not reach the preview service. Please try again.';
        $('sampleGallery').open = true;
      }
    }
  })();
  sync();
  await pendingRender;
  pendingRender = null;
  renderProgress(false);
  sync();
};
function clearUserData() {
  $('c1').checked = false;
  $('c2').checked = false;
  $('c2').onchange();
  $('ag').value = '';
  $('gf').value = '';
  erasePhoto();
}
$('del').onclick = clearUserData;
$('clearTryData').onclick = clearUserData;
sync();
// fit & returns
const dirOf = b => [b.direction, b.direction === 'Near true' ? 't3' : 't1'];
let brandSort = {key: 'ret', descending: true};
function renderBrands() {
  const columns = [['brand', 'Brand'], ['ret', 'Size returns'], ['ts', 'Too small'],
    ['tl', 'Too large'], ['direction', 'Direction'], ['gap', 'Chart gap'], ['chart', 'Chart']];
  const ordered = [...brands].sort((left, right) => {
    const {key, descending} = brandSort;
    const delta = key === 'brand' ? left.brand.localeCompare(right.brand) : (left[key] ?? 0) - (right[key] ?? 0);
    return descending ? -delta : delta;
  });
  const headers = columns.map(([key, label]) => {
    if (!['brand', 'ret', 'ts', 'tl', 'gap'].includes(key)) return `<th scope="col">${label}</th>`;
    const selected = key === brandSort.key;
    const order = brandSort.descending ? 'descending' : 'ascending';
    return `<th scope="col"${selected ? ' aria-sort="' + order + '"' : ''}><button type="button" data-sort="${key}">${label} <span aria-hidden="true">${selected ? (brandSort.descending ? '↓' : '↑') : '↕'}</span></button></th>`;
  }).join('');
  const rows = ordered.map(b => {
    const d = dirOf(b);
    const bad = b.chart_unreliable;
    return `<tr><th scope="row">${b.brand}</th><td>${b.ret}%<span class="meter" aria-hidden="true"><i style="width:${b.ret}%"></i></span></td><td>${b.ts}%</td><td>${b.tl}%</td><td><span class="tag ${d[1]}">${d[0]}</span></td><td>${b.gap === null ? '—' : b.gap} · n=${b.chart_n}</td><td>${bad ? '<span class="tag t1">Unreliable chart</span>' : 'OK'}</td></tr>`;
  }).join('');
  $('bt').innerHTML = `<thead><tr>${headers}</tr></thead><tbody>${rows}</tbody>`;
}
$('bt').onclick = event => {
  const button = event.target.closest('[data-sort]');
  if (!button) return;
  const key = button.dataset.sort;
  brandSort = {key, descending: key === brandSort.key ? !brandSort.descending : key !== 'brand'};
  renderBrands();
  $('bt').querySelector(`[data-sort="${key}"]`).focus();
};
function renderMessages() {
for (const brand of brands) {
  const card = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = brand.brand;
  const message = document.createElement('p');
  message.textContent = brand.msg;
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'copy';
  copy.textContent = 'Copy message';
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(brand.msg);
      toast('Draft message copied.');
    } catch {
      toast('Could not copy the draft. You can select and copy the message.');
    }
  };
  card.append(summary, message, copy);
  $('ms').append(card);
}
}
// Results and branding use the same generated, held-out report as the API.
const pct = value => value === null || value === undefined ? 'Not run' : value + '%';
const table = (headers, rows) => `<div class="wrap" tabindex="0"><table><thead><tr>${headers.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
function renderResults(report) {
  const overall = report.overall;
  const exchange = report.by_source['Kept after exchange'];
  const business = report.business;
  const statRow = (name,r) => [name, r.n, pct(r.exact.pct), pct(r.within.pct),
    `${pct(r.usual.pct)} → ${pct(r.model_on_usual_subset.pct)} (n=${r.usual.n})`, pct(r.first.pct)];
  const target = (name,score,goal) => `<div class="target"><b>${name} · ${score >= goal ? 'Target met' : 'Target missed'}</b><progress value="${score}" max="100" aria-label="${name} accuracy"></progress><div>${score}% actual · ≥${goal}% target</div></div>`;
  const sections = [statRow('Overall', overall), ...Object.entries(report.by_source).map(([key,row]) => statRow(key,row)),
    ...Object.entries(report.by_category).map(([key,row]) => statRow(key,row)),
    ...Object.entries(report.by_history).map(([key,row]) => statRow('History: '+key,row))];
  $('rb').innerHTML = `<div class="card"><h2>October fit evaluation</h2>
    <div class="target-grid">${target('Overall',overall.within.pct,85)}${target('Kept after exchange',exchange.within.pct,60)}</div>
    ${table(['Slice','Cases','Exact','Within acceptable','Usual size → model (matched)','First choice'],sections)}
    <p class="mu">The October set contains kept and exchanged orders. First choice scores zero on exchange cases by construction. Orders refunded for size without an exchange are excluded. The usual-size baseline uses cases with a known usual size.</p>
    <p class="mu">Lowest within-acceptable categories: ${report.worst_categories.join(', ')}.</p></div>
    <div class="card"><h2>Confidence calibration</h2>${table(['Evidence','Cases','Observed exact match'],Object.entries(report.reliability).map(([name,row])=>[name,row.n,pct((row.accuracy*100).toFixed(2))]))}</div>
    <div class="card"><h2>Returns projection · Women’s kurtas</h2>
    <p>Uses ${business.returns.heldout_pairs} October cases with a known usual size. Usual-size errors: ${business.returns.usual_wrong}. Model errors: ${business.returns.model_wrong}.</p>
    ${table(['Adoption assumption','Returns avoided','Refunds avoided','Logistics saved'],business.returns.scenarios.map(row=>[pct(Math.round(row.adoption*100)),row.avoided,row.refunds,money(row.logistics_inr)]))}
    <p class="mu">Scenarios multiply the Jan–Sep kurta size-return volume by 25 and apply the held-out relative error change at each adoption level. Assumes 60% refund share and ₹117 per return. These are projections.</p></div>
    <div class="card"><h2>Planned conversion experiment</h2>
    <p>Randomise by customer with sticky assignment. Primary metric is kurta PDP purchase conversion. Power target is 80% at a two-sided 5% significance level, with a 3.9% design baseline and 10% relative lift.</p>
    <p>Plan ${business.ab.weeks} whole weeks and ${business.ab.per_arm.toLocaleString()} sessions per arm. The plan assumes ${business.ab.company_monthly_kurta_sessions.toLocaleString()} eligible company sessions per month, scaling the September sample of ${business.ab.kurta_sessions.toLocaleString()} by ${business.ab.traffic_scale}. The four-week minimum keeps weeks 2–4 available for separate reporting. Verify company traffic and adjust for customer clustering.</p>
    <p>Measure size-related returns 30 days after purchase. Use intention-to-treat and report weeks 2–4 separately.</p></div>
    <div class="card warn"><h2>Try-on evaluation</h2><p>The 120-case grid has no approved scores in this deployment. Body-type and skin-tone results are not run.</p><p id="liveMetrics" class="mu">Loading live render measurements…</p></div>`;
  fetch('/api/metrics').then(response => {
    if (!response.ok) throw new Error();
    return response.json();
  }).then(v => {
    const cost = v.average_cost_inr !== null ? 'Measured mean cost ' + money(v.average_cost_inr) + '.'
      : v.estimated_cost_inr !== null ? 'Estimated cost ' + money(v.estimated_cost_inr.toFixed(2)) +
        ' assuming ' + money(v.estimated_gpu_hour_inr) + ' per GPU-hour for the full render time.'
      : 'Cost unmeasured.';
    $('liveMetrics').textContent = v.renders
      ? `${v.renders} successful logged renders. Server p50 ${v.p50_latency_s.toFixed(2)} s, p95 ${v.p95_latency_s.toFixed(2)} s. ${cost}`
      : 'No successful renders in the service log. Latency and cost are not run.';
  }).catch(() => { $('liveMetrics').textContent = 'Render measurements could not be loaded.'; });
}
fetch('/api/fit-results').then(response => {
  if (!response.ok) throw new Error();
  return response.json();
}).then(report => {
  const brandNames = [...new Set(Object.values(report.brands).flatMap(Object.keys))].sort();
  $('ub').append(...brandNames.map(name => new Option(name)));
  brands = Object.values(report.brands['Women · Kurtas']);
  const unreliable = brands.filter(brand => brand.chart_unreliable).length;
  $('fitSummary').textContent = `${brands.length} kurta brands · ${unreliable} charts flagged using consented kept orders`;
  renderBrands();
  renderMessages();
  renderResults(report);
}).catch(() => {
  $('rb').textContent = 'The evaluation report could not be loaded.';
  $('fitSummary').textContent = 'Brand evidence could not be loaded.';
});
