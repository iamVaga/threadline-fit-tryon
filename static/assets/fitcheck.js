const $ = id => document.getElementById(id);
const visible = (id, show) => $(id).classList.toggle('hid', !show);
const letterSizes = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL'];
const jeansSizes = ['24', '26', '28', '30', '32', '34', '36', '38', '40', '42'];
const views = ['workspace', 'fit', 'res'];
const navigation = document.querySelectorAll('[data-view]');
const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 });
const money = amount => inr.format(Number(amount));
let activeView = 'workspace';
let activeTool;
let selectedGarment;
let inventory = [];
let inventoryRequest;
const inventoryPageSize = 8;
let inventoryLimit = inventoryPageSize;
let brands = [];
let fitProducts = [];
let fitCatalogueRequest;
let recommendRequest = 0;

function showView(view) {
  if (activeView === view) return;
  if (activeView === 'workspace') stopCamera();
  activeView = view;
  for (const id of views) visible(id, id === view);
  for (const button of navigation) {
    if (button.hasAttribute('aria-pressed')) {
      button.setAttribute('aria-pressed', String(button.dataset.view === view));
    }
  }
  if (view !== 'workspace') loadEvidence();
  window.scrollTo(0, 0);
  $(view).focus({ preventScroll: true });
}

function renderWorkspace() {
  const pickerOpen = $('inventoryStep').open;
  const sizeOpen = activeTool === 'sizeStep' && !pickerOpen;
  const tryOpen = activeTool === 'tryStep' && !pickerOpen;
  visible('sizeStep', sizeOpen);
  visible('tryStep', tryOpen);
  $('selectedSize').setAttribute('aria-expanded', String(sizeOpen));
  $('selectedTry').setAttribute('aria-expanded', String(tryOpen));
  if (!tryOpen) stopCamera();
}

function openTool(tool) {
  activeTool = tool;
  $('inventoryStep').open = false;
  renderWorkspace();
  if (tool === 'sizeStep') loadFitProducts();
  else {
    loadPhotoConfig();
    loadGallery();
  }
  $(tool).focus({ preventScroll: true });
}

function openInventory() {
  $('inventoryStep').open = true;
  loadInventory();
  renderWorkspace();
  focusSection($('inventoryStep').querySelector('summary'));
}

for (const button of navigation) button.onclick = () => showView(button.dataset.view);
$('homeLink').onclick = event => {
  if (activeView === 'workspace') return;
  event.preventDefault();
  showView('workspace');
};
$('inventoryStep').addEventListener('toggle', () => {
  if ($('inventoryStep').open) loadInventory();
  renderWorkspace();
});

function focusSection(element) {
  element.focus({ preventScroll: true });
  element.scrollIntoView({ block: 'nearest' });
}

function resetSizeForm(sku) {
  recommendRequest++;
  $('skuLookup').value = sku;
  $('recommendStatus').textContent = '';
  $('technicalTiming').textContent = '';
  visible('out', false);
  visible('fitInputs', !!sku.trim());
  updateUsualSizes();
}

function renderSelection() {
  const item = selectedGarment;
  visible('selection', !!item);
  visible('fitUnavailable', !!item && !item.hasFit);
  if (!item) return;
  $('selectionName').textContent = item.name;
  $('selectionMeta').textContent = [item.brand, item.category, !item.image && 'No inventory photo'].filter(Boolean).join(' · ');
  const thumbnail = $('selectionPhoto');
  if (item.image) thumbnail.src = item.image;
  else thumbnail.removeAttribute('src');
  visible('selectionPhoto', !!item.image);
}

function setGarment(item) {
  if (selectedGarment?.sku === item.sku && selectedGarment.image === item.image) return;
  selectedGarment = item;
  const source = $('garmentSource');
  source.replaceChildren(new Option('Upload a garment photo', ''));
  if (item.image) {
    source.add(new Option('Use selected garment', 'inventory'));
    source.value = 'inventory';
  }
  if (item.garmentType) $('garmentType').value = item.garmentType;
  $('gf').value = '';
  renderSelection();
  updateInventorySelection();
  updateGarment();
}

function fromFitCatalogue(item) {
  const photographed = inventory.find(garment => garment.sku === item.sku);
  if (photographed) return photographed;
  const kind = item.category.split(' · ')[1];
  const garmentType = { Dresses: 'one-pieces', Kurtas: 'one-pieces', Jeans: 'bottoms' }[kind] || 'tops';
  return { ...item, hasFit: true, garmentType };
}

function updateInventorySelection() {
  for (const button of $('inventoryGrid').querySelectorAll('button')) {
    button.setAttribute('aria-pressed', String(button.dataset.sku === selectedGarment?.sku));
  }
}

function loadInventory() {
  if (inventoryRequest) return;
  $('inventoryStatus').textContent = 'Loading garments…';
  inventoryRequest = fetch('/api/catalog').then(response => {
    if (!response.ok) throw new Error();
    return response.json();
  }).then(items => {
    inventory = items.flatMap(item => {
      const photo = item.variants.find(variant => variant.image);
      return photo ? [{
        sku: item.sku_id,
        name: item.name.trim(),
        brand: item.brand,
        category: item.category_label || 'Other',
        garmentType: item.garment_category,
        hasFit: !item.free,
        image: photo.image,
      }] : [];
    });
    const featured = ['in-67b3ab82bb.jpg', 'in-a6f53586f0.jpg', 'fs-18.png', 'in-b5e04df86a.jpg'];
    const rank = item => {
      const index = featured.indexOf(item.image.split('/').pop());
      return index < 0 ? featured.length : index;
    };
    inventory.sort((left, right) => rank(left) - rank(right));
    const categories = [...new Set(inventory.map(item => item.category))].sort();
    $('inventoryCategory').append(...categories.map(value => new Option(value)));
    const photographed = inventory.find(item => item.sku === selectedGarment?.sku);
    if (photographed && !selectedGarment.image) setGarment(photographed);
    renderInventory();
  }).catch(() => {
    inventoryRequest = null;
    $('inventoryStatus').textContent = 'Inventory could not load. Reopen Choose a garment to retry, or upload a garment photo.';
  });
}

function renderInventory() {
  const category = $('inventoryCategory').value;
  const query = $('inventorySearch').value.trim().toLowerCase();
  const matches = inventory.filter(item => {
    return (!category || item.category === category) &&
      [item.name, item.brand, item.category].join(' ').toLowerCase().includes(query);
  });
  $('inventoryStatus').textContent = matches.length
    ? matches.length + (matches.length === 1 ? ' garment' : ' garments')
    : 'No matching garments. Try a different name or category.';
  $('inventoryGrid').replaceChildren(...matches.slice(0, inventoryLimit).map((item, index) => {
    const row = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.sku = item.sku;
    button.setAttribute('aria-pressed', String(selectedGarment?.sku === item.sku));
    const image = document.createElement('img');
    image.src = item.image;
    image.alt = '';
    image.loading = index < 4 ? 'eager' : 'lazy';
    image.decoding = 'async';
    const name = document.createElement('span');
    name.className = 'inventory-name';
    name.textContent = item.name;
    const categoryLabel = document.createElement('small');
    categoryLabel.textContent = item.category;
    button.append(image, name, categoryLabel);
    button.onclick = () => {
      if (selectedGarment?.sku !== item.sku) {
        setGarment(item);
        resetSizeForm(item.hasFit ? item.sku : '');
      }
      $('inventoryStep').open = false;
      renderWorkspace();
      focusSection($('selection'));
    };
    row.append(button);
    return row;
  }));
  visible('inventoryMore', inventoryLimit < matches.length);
}

function filterInventory() {
  inventoryLimit = inventoryPageSize;
  if (inventoryRequest) renderInventory();
}
$('inventorySearch').oninput = filterInventory;
$('inventoryCategory').onchange = filterInventory;
$('inventoryMore').onclick = () => {
  const previous = inventoryLimit;
  inventoryLimit += inventoryPageSize;
  renderInventory();
  $('inventoryGrid').children[previous]?.querySelector('button').focus();
};
$('changeGarment').onclick = openInventory;
$('tryPickGarment').onclick = openInventory;
$('selectedTry').onclick = () => openTool('tryStep');
$('selectedSize').onclick = () => openTool('sizeStep');

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
    const brandNames = [...new Set(items.map(item => item.brand))].sort();
    $('ub').append(...brandNames.map(name => new Option(name)));
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
      setGarment(fromFitCatalogue(item));
      resetSizeForm(item.sku);
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

$('skuLookup').oninput = () => resetSizeForm($('skuLookup').value);
loadInventory();
function runSampleCase() {
  $('testLookup').value = 'FT-10026';
  rec(true);
}
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
    $('resultTitle').textContent = lookup ? 'Demo recommendation' : 'Recommended size';
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
    if (activeView === 'workspace' && !$('sizeStep').classList.contains('hid')) focusSection($('out'));
    if (!lookup && result.sku_id !== selectedGarment?.sku) {
      const fitItem = fitProducts.find(item => item.sku === result.sku_id);
      if (fitItem) setGarment(fromFitCatalogue(fitItem));
    }
    if (lookup && !selectedGarment) {
      $('skuLookup').value = result.sku_id;
      visible('fitInputs', true);
      updateUsualSizes();
    }
    if (lookup && result.needs_input && !selectedGarment) {
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
let photoConfigLoaded = false, photoConfigFailed = false, remoteBackend = null;
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
let galleryRequest;
function loadGallery() {
  if (galleryRequest) return;
  galleryRequest = fetch('/api/gallery').then(response => {
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
}
const day = 24 * 60 * 60 * 1000;
const adult = () => Number.isInteger(Number($('ag').value)) && Number($('ag').value) >= 18;
let thumbFile = null, thumbUrl = null;

const inventoryGarment = () => $('garmentSource').value === 'inventory' ? selectedGarment : null;
const garmentCategory = () => inventoryGarment()?.garmentType || $('garmentType').value;

function updateGarment() {
  const crop = garmentCategory() === 'tops'
    ? 'Keep your head to hips in frame.' : 'Keep your head to feet in frame.';
  $('photoGuide').textContent = 'Use a clear photo of one person. ' + crop;
  $('cameraGuide').textContent = crop + ' Use even lighting and a plain background.';
  visible('garmentUpload', !inventoryGarment());
  visible('tryPickGarment', !inventoryGarment());
  $('msg').textContent = '';
  clearPreview();
  sync();
}
$('garmentSource').onchange = () => {
  $('gf').value = '';
  updateGarment();
};
$('garmentType').onchange = updateGarment;
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
  if (photoConfigFailed) need.push('Reload the page to try the preview again.');
  else if (!photoConfigLoaded) need.push('Loading photo consent…');
  else if (!inventoryGarment() && !$('gf').files[0]) need.push('Add a garment photo or choose a sample.');
  else if (!file) need.push('Choose your photo.');
  else if (!$('c1').checked || !adult()) need.push('Enter your age and agree to photo processing.');
  $('ph').disabled = deleting;
  $('cameraStart').disabled = !photoConfigLoaded || !adult() || !$('c1').checked || deleting;
  if ($('cameraStart').disabled) stopCamera();
  $('tryNeeds').textContent = need.join(' ');
  $('rn').disabled = need.length > 0 || !!pendingRender || deleting;
  $('del').disabled = deleting;
  $('clearTryData').disabled = deleting;
  visible('clearTryData', !!file || !!$('gf').files[0] || !!$('ag').value || $('c1').checked || deleting);

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
  $('ph').files = transfer.files;
  $('ph').dispatchEvent(new Event('change', {bubbles: true}));
};

let photoConfigRequest;
function loadPhotoConfig() {
  if (photoConfigRequest) return;
  photoConfigFailed = false;
  sync();
  photoConfigRequest = fetch('/api/tryon-config').then(response => {
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
    photoConfigFailed = true;
    photoConfigRequest = null;
    sync();
  });
}
// The hosting tier restarts the app now and then. Retry network failures and gateway errors (502/504)
// a couple of times before telling the user; real answers from the app (even errors) are never retried.
async function postWithRetry(url, body) {
  for (let attempt = 0; ; attempt++) {
    try {
      const response = await fetch(url, {method: 'POST', body});
      if (![502, 504].includes(response.status) || attempt >= 2) return response;
    } catch (error) {
      if (attempt >= 2) throw error;
    }
    $('msg').textContent = 'The preview service is waking up. Retrying…';
    await new Promise(resolve => setTimeout(resolve, 8000));
  }
}
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
  const garment = inventoryGarment();
  body.append('garment_category', garmentCategory());
  const description = garment?.name || { tops: 'top', bottoms: 'bottom', 'one-pieces': 'dress or one-piece' }[garmentCategory()];
  body.append('desc', description.slice(0, 160));
  const photo = $('ph').files[0];
  body.append('photo', photo);
  const garmentFile = $('gf').files[0];
  if (garment) body.append('sku_id', garment.sku);
  else if (garmentFile) body.append('garment', garmentFile);
  else return;
  $('rn').disabled = true;
  const validation = await checkClientPhoto(photo);
  if (requestGeneration !== generation || deleting || !$('c1').checked || !adult()) { sync(); return; }
  if (validation) { $('msg').textContent = validation; sync(); return; }
  $('msg').textContent = 'Checking the photo and preparing the preview…';
  renderProgress(true);
  pendingRender = (async () => {
    try {
      const response = await postWithRetry('/api/tryon', body);
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
updateGarment();
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
let evidenceRequest;
function loadEvidence() {
  if (evidenceRequest) return;
  $('rb').textContent = 'Loading results…';
  $('fitSummary').textContent = 'Loading brand evidence…';
  evidenceRequest = fetch('/api/fit-results').then(response => {
    if (!response.ok) throw new Error();
    return response.json();
  }).then(report => {
    brands = Object.values(report.brands['Women · Kurtas']);
    const unreliable = brands.filter(brand => brand.chart_unreliable).length;
    $('fitSummary').textContent = `${brands.length} kurta brands · ${unreliable} charts flagged using consented kept orders`;
    renderBrands();
    renderMessages();
    renderResults(report);
  }).catch(() => {
    evidenceRequest = null;
    $('rb').textContent = 'The evaluation report could not be loaded.';
    $('fitSummary').textContent = 'Brand evidence could not be loaded.';
  });
}
