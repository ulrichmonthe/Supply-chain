/* The column mapper, in the browser. A port of the tool's synonym table and scoring
   (backend/app/io/mapper.py) so a visitor can drop their own facility register and see
   it mapped and plotted without the file leaving the page. */
(function () {
  'use strict'
  const FIELDS = ['code', 'name', 'level', 'type', 'lat', 'lon', 'admin1', 'admin2', 'catchment_population', 'operating_status', 'dry_m3', 'hub_capable']
  const LABELS = { code: 'Code', name: 'Name', level: 'Level', type: 'Type', lat: 'Latitude', lon: 'Longitude', admin1: 'Province', admin2: 'District', catchment_population: 'Catchment population', operating_status: 'Status', dry_m3: 'Storage m³', hub_capable: 'Can be a store' }
  const SYNONYMS = {
    code: ['facility_code', 'hf_code', 'site_code', 'org_unit_code', 'orgunit_code', 'orgunit', 'org_unit', 'uid', 'id', 'facility_id', 'mfl_code', 'dhis2_uid'],
    name: ['facility_name', 'facility', 'site_name', 'org_unit_name', 'orgunit_name', 'health_facility', 'hf_name'],
    level: ['facility_level', 'tier', 'hierarchy_level'],
    type: ['facility_type', 'category', 'ownership_type', 'site_type'],
    lat: ['latitude', 'y', 'gps_lat', 'lat_dd', 'latitude_dd', 'gps_latitude', 'latitude_gps', 'y_coord', 'ycoord'],
    lon: ['longitude', 'lng', 'long', 'x', 'gps_lon', 'lon_dd', 'longitude_dd', 'gps_long', 'gps_longitude', 'longitude_gps', 'x_coord', 'xcoord'],
    admin1: ['province', 'region', 'state', 'admin_1', 'adm1', 'county'],
    admin2: ['district', 'admin_2', 'adm2', 'sub_county', 'llg'],
    catchment_population: ['population', 'catchment', 'catchment_pop', 'pop', 'population_served', 'people_served', 'pop_served', 'catchment_population_served', 'target_population'],
    operating_status: ['status', 'functional_status', 'operational_status', 'open'],
    dry_m3: ['storage_m3', 'dry_storage_m3', 'ambient_m3', 'storage_volume'],
    hub_capable: ['is_hub', 'hub', 'warehouse', 'is_warehouse'],
  }
  const norm = (s) => String(s || '').trim().toLowerCase().replace(/ /g, '_').replace(/-/g, '_').replace(/[^a-z0-9_]/g, '')
  function score(column, field) {
    const h = norm(column)
    if (h === field) return 3
    if ((SYNONYMS[field] || []).map(norm).includes(h)) return 2
    if (field.length > 3 && (h.endsWith('_' + field) || h.startsWith(field + '_'))) return 1
    return 0
  }
  function guess(columns) {
    const cands = []
    for (const field of FIELDS) for (const col of columns) { const s = score(col, field); if (s) cands.push([s, field, col]) }
    cands.sort((a, b) => b[0] - a[0] || a[1].localeCompare(b[1]))
    const mapping = {}, taken = new Set()
    for (const [, field, col] of cands) { if (mapping[field] || taken.has(col)) continue; mapping[field] = col; taken.add(col) }
    return mapping
  }
  function parseCsv(text) {
    const first = text.split(/\r?\n/, 1)[0]
    const delim = [',', ';', '\t', '|'].map((d) => [d, first.split(d).length]).sort((a, b) => b[1] - a[1])[0][0]
    const rows = []; let row = [], cell = '', q = false
    for (let i = 0; i < text.length; i++) {
      const ch = text[i]
      if (q) { if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++ } else q = false } else cell += ch }
      else if (ch === '"') q = true
      else if (ch === delim) { row.push(cell); cell = '' }
      else if (ch === '\n') { row.push(cell); rows.push(row); row = []; cell = '' }
      else if (ch !== '\r') cell += ch
    }
    if (cell.length || row.length) { row.push(cell); rows.push(row) }
    const header = rows.shift() || []
    return { header: header.map((h) => h.trim()), rows: rows.filter((r) => r.some((c) => c.trim())).map((r) => Object.fromEntries(header.map((h, i) => [h.trim(), (r[i] || '').trim()]))), delimiter: delim }
  }

  const drop = document.getElementById('drop'), input = document.getElementById('csv-file'), status = document.getElementById('mapper-status'), fields = document.getElementById('map-fields'), mapEl = document.getElementById('mapper-map')
  if (!drop) return
  let parsed = null, mapping = {}, land = null
  const svg = mapEl.querySelector('svg'), ns = 'http://www.w3.org/2000/svg'
  const landReady = fetch('app/api/basemap.json').then((r) => r.json()).then((g) => { land = g }).catch(() => {})
  function plot() {
    const latC = mapping.lat, lonC = mapping.lon
    const pts = []
    for (const r of parsed.rows) {
      const lat = parseFloat(r[latC]), lon = parseFloat(r[lonC])
      if (!isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) continue
      pts.push({ lat, lon, name: r[mapping.name] || r[mapping.code] || '', pop: parseFloat(r[mapping.catchment_population]) || 0 })
    }
    const W = 760, H = 440
    while (svg.firstChild) svg.removeChild(svg.firstChild)
    const el = (tag, attrs) => { const n = document.createElementNS(ns, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); svg.appendChild(n); return n }
    el('rect', { width: W, height: H, fill: '#0a1420' })
    if (pts.length) {
      const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon)
      const spanLat = Math.max(0.5, Math.max(...lats) - Math.min(...lats)), spanLon = Math.max(0.5, Math.max(...lons) - Math.min(...lons))
      // Keep the aspect honest: degrees of longitude shrink with latitude.
      const midLat = (Math.max(...lats) + Math.min(...lats)) / 2, k = Math.cos((midLat * Math.PI) / 180)
      const scale = Math.min((W * 0.86) / (spanLon * k), (H * 0.86) / spanLat)
      const cx = (Math.max(...lons) + Math.min(...lons)) / 2
      const X = (lon) => (W / 2 + (lon - cx) * k * scale).toFixed(1), Y = (lat) => (H / 2 - (lat - midLat) * scale).toFixed(1)
      if (land) for (const f of land.features) {
        const rings = f.geometry.type === 'Polygon' ? f.geometry.coordinates : f.geometry.type === 'MultiPolygon' ? f.geometry.coordinates.flat() : []
        for (const ring of rings) el('polygon', { points: ring.map(([lon, lat]) => X(lon) + ',' + Y(lat)).join(' '), fill: '#16263a', stroke: '#2d4360', 'stroke-width': 0.8 })
      }
      const maxPop = Math.max(1, ...pts.map((p) => p.pop))
      for (const p of pts) {
        el('circle', { cx: X(p.lon), cy: Y(p.lat), r: (3 + 6 * Math.sqrt(p.pop / maxPop)).toFixed(1), fill: '#3fb984', stroke: '#0a1420', 'stroke-width': 1 })
        if (pts.length <= 60) el('text', { x: (+X(p.lon) + 8).toFixed(1), y: (+Y(p.lat) + 4).toFixed(1), 'font-size': 10.5, fill: '#dbe6f2', 'font-family': 'Public Sans, sans-serif', stroke: '#0a1420', 'stroke-width': 3, 'paint-order': 'stroke' }).textContent = p.name
      }
    } else {
      el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', fill: '#8fa2b8', 'font-size': 14, 'font-family': 'Public Sans, sans-serif' }).textContent = 'No row has a usable coordinate yet'
    }
    const bad = parsed.rows.length - pts.length
    status.innerHTML = `<b>${pts.length}</b> of ${parsed.rows.length} rows plotted from <b>${parsed.header.length}</b> columns (delimiter <code>${parsed.delimiter === '\t' ? 'tab' : parsed.delimiter}</code>). ${bad ? `<b>${bad}</b> rows have no usable coordinate and would be flagged, not dropped silently.` : 'Every row has a coordinate.'} Nothing was uploaded.`
  }
  function renderFields() {
    fields.hidden = false
    fields.innerHTML = '<div class="row"><b>Our column</b><b>Your column</b><b>First value</b></div>' + FIELDS.map((f) => {
      const opts = ['<option value="">—</option>'].concat(parsed.header.map((h) => `<option value="${h.replace(/"/g, '&quot;')}"${mapping[f] === h ? ' selected' : ''}>${h.replace(/</g, '&lt;')}</option>`)).join('')
      const sample = mapping[f] && parsed.rows[0] ? parsed.rows[0][mapping[f]] : ''
      return `<div class="row"><label for="mf-${f}">${LABELS[f]}${['code', 'name', 'lat', 'lon'].includes(f) ? ' *' : ''}</label><select id="mf-${f}" data-field="${f}">${opts}</select><span class="sample">${String(sample).replace(/</g, '&lt;')}</span></div>`
    }).join('')
    fields.querySelectorAll('select').forEach((s) => s.addEventListener('change', () => { mapping[s.dataset.field] = s.value || undefined; renderFields(); plot() }))
  }
  function load(text, label) {
    parsed = parseCsv(text)
    mapping = guess(parsed.header)
    const missing = ['code', 'name', 'lat', 'lon'].filter((f) => !mapping[f])
    renderFields()
    if (missing.length) status.innerHTML = `Read <b>${parsed.rows.length}</b> rows from ${label}. Could not find a column for <b>${missing.map((f) => LABELS[f]).join(', ')}</b>; pick it below.`
    if (mapping.lat && mapping.lon) landReady.then(plot)
  }
  const readFile = (file) => { if (!file) return; const fr = new FileReader(); fr.onload = () => load(String(fr.result), `<code>${file.name.replace(/</g, '&lt;')}</code>`); fr.readAsText(file) }
  input.addEventListener('change', () => readFile(input.files[0]))
  drop.addEventListener('click', (e) => { if (e.target !== input) input.click() })
  drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click() } })
  ;['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over') }))
  ;['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over') }))
  drop.addEventListener('drop', (e) => readFile(e.dataTransfer.files[0]))
  const sample = document.getElementById('mapper-sample')
  if (sample) sample.addEventListener('click', async () => {
    const nodes = await fetch('app/api/nodes.json').then((r) => r.json())
    const pick = nodes.filter((n) => n.level >= 2).slice(0, 20)
    const csv = ['Facility Code;Health Facility;Province;District;Latitude;Longitude;Population Served;Functional Status'].concat(pick.map((n) => [n.code, n.name, n.admin1 || '', n.admin2 || '', n.lat, n.lon, n.catchment_population || '', n.operating_status].map((v) => String(v).includes(';') ? `"${v}"` : v).join(';'))).join('\n')
    load(csv, 'the demo sample (headers deliberately not ours: "Health Facility", "Population Served", semicolons)')
  })
  window.HSCNMapper = { guess, parseCsv, score }
})()
