const J = (r) => { if (!r.ok) throw new Error(r.status); return r.json() }

export const api = {
  // auth
  login: (username, password) => fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('login'), { detail: j.detail }); return j }),
  verifyToken: (token) => fetch('/api/auth/verify?token=' + encodeURIComponent(token || '')).then(J),

  status: () => fetch('/api/status').then(J),
  listDocuments: () => fetch('/api/documents').then(J),
  getDocument: (id) => fetch(`/api/documents/${id}`).then(J),
  deleteDocument: (id) => fetch(`/api/documents/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('del'), { detail: j.detail }); return j }),
  clearAllDocuments: (wipeMasters) => fetch('/api/documents/clear-all' + (wipeMasters ? '?wipe_masters=true' : ''), { method: 'DELETE' }).then(J),
  imageUrl: (id) => `/api/documents/${id}/image`,
  upload: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch('/api/documents/upload', { method: 'POST', body: fd }).then(J)
  },
  confirm: (id, data, train) =>
    fetch(`/api/documents/${id}/confirm`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data, train }),
    }).then(J),
  // pull LR No / LR Date / Transporter / Book City in from the LR register.
  // lrEntryId links a row the user picked from the suggestions instead of relying
  // on the invoice-no / LR-no match.
  fetchTransport: (id, lrEntryId) => fetch(
    `/api/documents/${id}/fetch-transport` + (lrEntryId ? `?lr_entry_id=${lrEntryId}` : ''),
    { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('lrfetch'), { detail: j.detail }); return j }),
  exportUrl: (id, format) => `/api/documents/${id}/export?format=${format}`,
  listSuppliers: () => fetch('/api/suppliers').then(J),
  getSupplier: (id) => fetch(`/api/suppliers/${id}`).then(J),

  // purchases / GRN
  listPurchases: () => fetch('/api/purchases').then(J),
  getPurchase: (id) => fetch(`/api/purchases/${id}`).then(J),
  buildGrn: (docId) => fetch(`/api/purchases/from-document/${docId}`, { method: 'POST' }).then(J),
  postGrn: (id) => fetch(`/api/purchases/${id}/post`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('post'), { detail: j.detail }); return j }),
  // reverse a posted GRN back to draft (guarded: payments / debit notes / dispatches)
  unpostCheck: (id) => fetch(`/api/purchases/${id}/unpost-check`).then(J),
  unpostGrn: (id) => fetch(`/api/purchases/${id}/unpost`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('unpost'), { detail: j.detail }); return j }),
  deletePurchase: (id) => fetch(`/api/purchases/${id}`, { method: 'DELETE' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('del'), { detail: j.detail }); return j }),
  // size breakup of one billed line — [] clears it
  setLineSplits: (lineId, rows) => fetch(`/api/purchases/lines/${lineId}/splits`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('splits'), { detail: j.detail }); return j }),
  // set a line's category master mapping ('' clears it, back to auto)
  editLine: (lineId, fields) => fetch(`/api/purchases/lines/${lineId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('line'), { detail: j.detail }); return j }),
  // pin a line (or one size of it) to a product by scanned QR / barcode / SKU
  scanLineCode: (lineId, code, split_id) => fetch(`/api/purchases/lines/${lineId}/scan`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, split_id }) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('scan'), { detail: j.detail }); return j }),

  // inventory
  inventorySummary: () => fetch('/api/inventory/summary').then(J),
  listProducts: () => fetch('/api/inventory/products').then(J),
  getProduct: (id) => fetch(`/api/inventory/products/${id}`).then(J),
  // no editProduct: a product is what its GRN made it — correct it by unposting
  // the GRN, fixing the line and posting again (stock is corrected via adjustStock)
  adjustStock: (id, new_qty, note) => fetch(`/api/inventory/products/${id}/adjust-stock`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ new_qty, note }) }).then(J),
  generateBarcode: (id) => fetch(`/api/inventory/products/${id}/generate-barcode`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('bc'), { detail: j.detail }); return j }),
  lookupByCode: (code) => fetch('/api/inventory/lookup?code=' + encodeURIComponent(code))
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('lk'), { detail: j.detail }); return j }),
  labelUrl: (id) => `/api/inventory/products/${id}/label`,
  labelsUrl: (ids, status) => {
    const p = []
    if (ids && ids.length) p.push('ids=' + ids.join(','))
    if (status) p.push('status=' + status)
    return '/api/inventory/labels' + (p.length ? '?' + p.join('&') : '')
  },
  // dropdown option sets (same lists the phone app uses, incl. sizes)
  productOptions: () => fetch('/api/inventory/product-options').then(J),
  barcodeSvgUrl: (id) => `/api/inventory/products/${id}/barcode.svg`,
  qrSvgUrl: (id) => `/api/inventory/products/${id}/qr.svg`,
  // map a free-text description onto the Product Category master
  categorize: (description) =>
    fetch('/api/inventory/categorize?description=' + encodeURIComponent(description || '')).then(J),

  // stock outward
  listOutwards: () => fetch('/api/outward').then(J),
  getOutward: (id) => fetch(`/api/outward/${id}`).then(J),
  createOutward: (body) => fetch('/api/outward', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(J),
  postOutward: (id, allowNeg) => fetch(`/api/outward/${id}/post?allow_negative=${!!allowNeg}`, { method: 'POST' })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('post'), { detail: j.detail }); return j }),

  // payments
  pendingBills: (supplierId) => fetch(`/api/payments/pending${supplierId ? '?supplier_id=' + supplierId : ''}`).then(J),
  listPayments: () => fetch('/api/payments').then(J),
  createPayment: (body) => fetch('/api/payments', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(J),
  supplierLedger: (id) => fetch(`/api/payments/supplier/${id}/ledger`).then(J),

  // purchase returns
  listReturns: () => fetch('/api/returns').then(J),
  getReturn: (id) => fetch(`/api/returns/${id}`).then(J),
  buildReturn: (purchaseId) => fetch(`/api/returns/from-purchase/${purchaseId}`, { method: 'POST' }).then(J),
  postReturn: (id, body) => fetch(`/api/returns/${id}/post`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(async r => { const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error('post'), { detail: j.detail }); return j }),

  // masters
  categories: (q) => fetch('/api/masters/categories' + (q ? '?q=' + encodeURIComponent(q) : '')).then(J),
  agents: () => fetch('/api/masters/agents').then(J),
  transports: () => fetch('/api/masters/transports').then(J),

  // LR entry
  lrExtract: (file) => { const fd = new FormData(); fd.append('file', file);
    return fetch('/api/lr/extract', { method: 'POST', body: fd }).then(J) },
  lrSave: (document_id, rows) => fetch('/api/lr/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_id, rows }) }).then(J),
  lrList: () => fetch('/api/lr').then(J),
  // freight settlement on delivery (mode, who paid, cash receiver's mobile)
  lrSettle: (id, fields) => fetch(`/api/lr/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields) }).then(J),

  // reports
  reportCatalogue: () => fetch('/api/reports').then(J),
  runReport: (key) => fetch(`/api/reports/${key}`).then(J),
  reportCsvUrl: (key) => `/api/reports/${key}/csv`,

  // settings / vision
  getSettings: () => fetch('/api/settings').then(J),
  setVisionKey: (api_key, model) => fetch('/api/settings/vision', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key, model }) }).then(J),
  listModels: () => fetch('/api/settings/models').then(J),
  setModel: (model) => fetch('/api/settings/model', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }) }).then(J),
  turnOffVision: () => fetch('/api/settings/vision/off', { method: 'POST' }).then(J),
}
