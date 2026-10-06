/*
 * Static demo shim.
 *
 * The real tool is a FastAPI backend with a MILP solver behind it, which a static
 * host cannot run. Every answer the interface can ask for has instead been solved
 * ahead of time and written to ./api/ — seven scenarios across thirteen months,
 * every result, every seasonal view. This file makes fetch read those files.
 *
 * The interface is the unmodified build. It does not know it is in a demo.
 *
 * What is faithful: the map, the network, the scorecard, the equity panel, the
 * month-by-month re-solve, the roadmap, the exports. Those are real solver output.
 * What cannot work: anything that writes (creating scenarios, moving a lever to a
 * value nobody pre-solved, uploading a workbook, reaching a live LMIS). Those
 * return a plain message saying so rather than failing silently.
 */
(function () {
  'use strict'

  var API = '/api'
  var ROOT = new URL('./api/', document.baseURI).href
  var nativeFetch = window.fetch.bind(window)

  // Which pre-solved snapshot the interface is currently looking at.
  var state = { key: 'initial', months: {} }
  var lastScenarios = []

  var json = function (body, status) {
    return new Response(JSON.stringify(body), {
      status: status || 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // A refusal the interface will show in its error banner, in plain words.
  var refuse = function (message) {
    return json({ detail: message }, 503)
  }

  var file = function (rel) {
    return nativeFetch(ROOT + rel, { cache: 'no-store' })
  }

  var readFile = function (rel) {
    return file(rel).then(function (r) {
      if (!r.ok) throw new Error(rel + ' is not part of this demo')
      return r.json()
    })
  }

  var keyFor = function (sid, month) {
    return sid + '-' + (month === null || month === undefined ? 'annual' : month)
  }

  var snapshot = function () {
    return readFile('snap/' + state.key + '.json').then(function (snap) {
      lastScenarios = snap.scenarios
      snap.scenarios.forEach(function (s) {
        state.months[s.id] = (s.levers || {}).month ?? null
      })
      return snap
    })
  }


  // ---- the sandbox ---------------------------------------------------------------
  // A static page cannot run the server, but a visitor should still be able to open a
  // country, add a facility and bring a facility list in, which is the first thing
  // anyone tries. Those three live here, in this browser's session storage, merged
  // into what the page serves. Everything else that would write still says so.
  var SANDBOX_KEY = 'hscn.demo.sandbox.v1'
  var sandbox = { countries: [], nodes: {}, audit: [], batches: {}, nextId: 100000, nextCountry: 100, nextBatch: 9000 }
  try {
    var kept = window.sessionStorage.getItem(SANDBOX_KEY)
    if (kept) sandbox = Object.assign(sandbox, JSON.parse(kept))
  } catch (e) { /* no storage: the sandbox lasts for this page only */ }
  var persist = function () { try { window.sessionStorage.setItem(SANDBOX_KEY, JSON.stringify(sandbox)) } catch (e) { /* ignore */ } }
  var nowIso = function () { return new Date().toISOString() }
  var authorOf = function (init) {
    var h = (init && init.headers) || {}
    return h['X-Author'] || h['x-author'] || 'anonymous'
  }
  var bodyOf = function (init) { try { return JSON.parse((init && init.body) || '{}') } catch (e) { return {} } }
  var countryIdOf = function (path) { var m = path.match(/^\/countries\/(\d+)/); return m ? Number(m[1]) : null }
  var sandboxCountry = function (id) { return sandbox.countries.filter(function (c) { return c.id === id })[0] || null }
  var sandboxNodes = function (cid) { return sandbox.nodes[cid] || [] }
  var isSandboxNode = function (id) { return id >= 100000 }
  var sandboxNote = ' (kept in this browser only: the demo has no server)'

  var makeNode = function (cid, f) {
    return {
      id: sandbox.nextId++, code: String(f.code || '').trim(), name: String(f.name || '').trim(), level: f.level == null ? 3 : Number(f.level),
      type: f.type || 'health_facility', lat: Number(f.lat), lon: Number(f.lon), geocode_confidence: 0.7, geocode_source: 'manual',
      admin1: f.admin1 || null, admin2: f.admin2 || null, capacity: {}, operating_status: f.operating_status || 'operational',
      catchment_population: Number(f.catchment_population || 0), terrain_class: f.terrain_class || 'mainland_road',
      hub_capable: !!f.hub_capable, hub_fixed_cost: 0, hub_open_capex: 0, hub_throughput_m3: 0, external_ids: {}, derivations: {},
      retired_at: null, retired_reason: '',
    }
  }
  var auditRow = function (cid, type, ref, field, value, who, why) {
    var row = { id: sandbox.nextId++, country_id: cid, entity_type: type, entity_ref: ref, field: field, old_value: null, new_value: value,
      provenance: 'manual_override', confidence_marker: 'I', rationale: why || '', actor: 'analyst', author_claim: who, batch_id: null,
      status: 'applied', reverts_id: null, created_at: nowIso() }
    sandbox.audit.unshift(row)
    return row
  }
  var allNodes = function (cid) {
    if (cid === 1) return readFile('nodes.json').then(function (base) { return base.concat(sandboxNodes(1)) })
    return Promise.resolve(sandboxNodes(cid))
  }
  var zeroed = function (value) {
    if (Array.isArray(value)) return []
    if (value && typeof value === 'object') { var out = {}; Object.keys(value).forEach(function (k) { out[k] = zeroed(value[k]) }); return out }
    if (typeof value === 'number') return 0
    return value
  }
  var overviewFor = function (cid) {
    return readFile('overview.json').then(function (base) {
      var added = sandboxNodes(cid)
      var people = added.reduce(function (s, n) { return s + (n.catchment_population || 0) }, 0)
      if (cid === 1) {
        var merged = JSON.parse(JSON.stringify(base))
        merged.counts.nodes += added.length
        merged.counts.facilities += added.filter(function (n) { return n.level >= 2 }).length
        merged.totals.population += people
        return merged
      }
      var country = sandboxCountry(cid)
      var provinces = {}
      added.forEach(function (n) { if (n.admin1) provinces[n.admin1] = true })
      return {
        country: country,
        counts: { nodes: added.length, facilities: added.filter(function (n) { return n.level >= 2 }).length, hubs: added.filter(function (n) { return n.hub_capable }).length, hubs_operational: 0, edges: 0, scheduled_services: 0, products: 0 },
        totals: { population: people, annual_demand_m3: 0, provinces: Object.keys(provinces).sort() },
        estimated: { demand_rows: 0, demand_rows_estimated: 0, demand_share: 0, demand_m3_estimated_share: 0, facilities_with_estimated_storage: 0 },
        distance_provenance: { counts: {}, total_edges: 0, km_weighted_confidence: 0, osrm_configured: false },
        provenance: { rows: {}, m3_share: {}, illustrative_rows: 0, sentence: 'No demand loaded.' },
        services: [], vulnerability: {}, seasonality_profiles: base.seasonality_profiles, months: base.months,
      }
    })
  }

  // The column mapper, as the server has it: our fields, their synonyms, a score.
  var OUR = {
    Nodes: [['code', 'Unique facility code.', true], ['name', 'Facility name.', true], ['level', '0 central, 1 regional, 2 provincial, 3 facility.', false], ['type', 'Facility type.', false], ['lat', 'Decimal degrees.', true], ['lon', 'Decimal degrees.', true], ['admin1', 'Province.', false], ['admin2', 'District.', false], ['catchment_population', 'People served.', false], ['operating_status', 'operational | non_operational | planned', false], ['dry_m3', 'Ambient storage, cubic metres.', false], ['hub_capable', 'TRUE if it can supply others.', false]],
    Edges: [['code', 'Lane code.', true], ['from_node', 'Origin code.', true], ['to_node', 'Destination code.', true], ['mode', 'road | sea | air | river', true]],
    Products: [['sku', 'Product code.', true], ['name', 'Product name.', true]],
    Demand: [['node', 'Facility code.', true], ['product', 'Product SKU.', true], ['quantity', 'Units in the period.', true]],
  }
  var SYN = {
    code: ['facility_code', 'hf_code', 'site_code', 'org_unit_code', 'orgunit_code', 'orgunit', 'org_unit', 'uid', 'id', 'facility_id', 'mfl_code', 'dhis2_uid'],
    name: ['facility_name', 'facility', 'site_name', 'org_unit_name', 'orgunit_name', 'health_facility', 'hf_name'],
    level: ['facility_level', 'tier', 'hierarchy_level'], type: ['facility_type', 'category', 'ownership_type', 'site_type'],
    lat: ['latitude', 'y', 'gps_lat', 'lat_dd', 'latitude_dd', 'gps_latitude', 'latitude_gps', 'y_coord', 'ycoord'],
    lon: ['longitude', 'lng', 'long', 'x', 'gps_lon', 'lon_dd', 'longitude_dd', 'gps_long', 'gps_longitude', 'longitude_gps', 'x_coord', 'xcoord'],
    admin1: ['province', 'region', 'state', 'admin_1', 'adm1', 'county'], admin2: ['district', 'admin_2', 'adm2', 'sub_county', 'llg'],
    catchment_population: ['population', 'catchment', 'catchment_pop', 'pop', 'population_served', 'people_served', 'pop_served', 'target_population'],
    operating_status: ['status', 'functional_status', 'operational_status', 'open'], dry_m3: ['storage_m3', 'dry_storage_m3', 'ambient_m3', 'storage_volume'],
    hub_capable: ['is_hub', 'hub', 'warehouse', 'is_warehouse'],
    from_node: ['from', 'origin', 'from_code', 'source', 'hub'], to_node: ['to', 'destination', 'to_code', 'target', 'facility'], mode: ['transport_mode', 'transport'],
    sku: ['product_code', 'item_code', 'item', 'commodity_code', 'product_id'], node: ['facility', 'facility_code', 'orgunit', 'org_unit', 'site', 'hf_code'],
    product: ['sku', 'item', 'item_code', 'product_code', 'commodity'], quantity: ['qty', 'consumption', 'consumed', 'issued', 'amount', 'dispensed', 'demand', 'units'],
  }
  var norm = function (s) { return String(s || '').trim().toLowerCase().replace(/ /g, '_').replace(/-/g, '_').replace(/[^a-z0-9_]/g, '') }
  var scoreCol = function (col, field) {
    var h = norm(col)
    if (h === field) return 3
    if ((SYN[field] || []).map(norm).indexOf(h) !== -1) return 2
    if (field.length > 3 && (h.slice(-field.length - 1) === '_' + field || h.slice(0, field.length + 1) === field + '_')) return 1
    return 0
  }
  var guessMapping = function (columns, sheet) {
    var cands = []
    OUR[sheet].forEach(function (f) { columns.forEach(function (c) { var s = scoreCol(c, f[0]); if (s) cands.push([s, f[0], c]) }) })
    cands.sort(function (a, b) { return b[0] - a[0] || (a[1] < b[1] ? -1 : 1) })
    var mapping = {}, taken = {}
    cands.forEach(function (x) { if (mapping[x[1]] || taken[x[2]]) return; mapping[x[1]] = x[2]; taken[x[2]] = true })
    OUR[sheet].forEach(function (f) { if (!(f[0] in mapping)) mapping[f[0]] = null })
    return mapping
  }
  var guessSheet = function (columns) {
    var best = 'Nodes', bestScore = -1
    Object.keys(OUR).forEach(function (sheet) {
      var m = guessMapping(columns, sheet), required = OUR[sheet].filter(function (f) { return f[2] })
      var hit = required.filter(function (f) { return m[f[0]] }).length
      var score = hit / required.length * 10 + Object.keys(m).filter(function (k) { return m[k] }).length + (hit === required.length ? 100 : 0)
      if (score > bestScore) { best = sheet; bestScore = score }
    })
    return best
  }
  var parseCsv = function (text) {
    var first = text.split(/\r?\n/, 1)[0]
    var delim = [',', ';', '\t', '|'].map(function (d) { return [d, first.split(d).length] }).sort(function (a, b) { return b[1] - a[1] })[0][0]
    var rows = [], row = [], cell = '', q = false
    for (var i = 0; i < text.length; i++) {
      var ch = text[i]
      if (q) { if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++ } else q = false } else cell += ch }
      else if (ch === '"') q = true
      else if (ch === delim) { row.push(cell); cell = '' }
      else if (ch === '\n') { row.push(cell); rows.push(row); row = []; cell = '' }
      else if (ch !== '\r') cell += ch
    }
    if (cell.length || row.length) { row.push(cell); rows.push(row) }
    var header = (rows.shift() || []).map(function (h) { return h.trim() })
    var out = []
    rows.forEach(function (r, i) {
      if (!r.some(function (c) { return c.trim() })) return
      var o = { _row: i + 2 }
      header.forEach(function (h, j) { if (h) o[h] = (r[j] || '').trim() })
      out.push(o)
    })
    return { header: header.filter(Boolean), rows: out, delimiter: delim }
  }
  var fileText = function (init) {
    var body = init && init.body
    if (body && typeof body.get === 'function') {
      var f = body.get('file')
      return f && f.text ? f.text().then(function (t) { return { name: f.name || 'upload.csv', text: t, form: body } }) : Promise.reject(new Error('no file'))
    }
    return Promise.reject(new Error('no file'))
  }

  var sandboxRead = function (path) {
    var cid = countryIdOf(path)
    var m
    if (path === '/countries') return readFile('countries.json').then(function (base) { return json(base.concat(sandbox.countries)) })
    if (cid === 1 && sandbox.nodes[1] && sandbox.nodes[1].length) {
      if (path === '/countries/1/nodes' || path === '/countries/1/tables/nodes') return allNodes(1).then(json)
      if (path === '/countries/1/overview') return overviewFor(1).then(json)
      if (path === '/countries/1/audit') return readFile('audit.json').then(function (base) { return json(sandbox.audit.filter(function (a) { return a.country_id === 1 }).concat(base)) })
    }
    if ((m = path.match(/^\/nodes\/(\d+)\/demand$/)) && isSandboxNode(Number(m[1]))) return json([])
    if (cid === null || !sandboxCountry(cid)) return undefined
    // A country opened in this browser.
    if (path.match(/\/overview$/)) return overviewFor(cid).then(json)
    if (path.match(/\/(nodes|tables\/nodes)$/)) return json(sandboxNodes(cid))
    if (path.match(/\/nodes\/retired$/)) return json([])
    if (path.match(/\/audit$/)) return json(sandbox.audit.filter(function (a) { return a.country_id === cid }))
    if (path.match(/\/(edges|products|studies|connections|column-mappings|tables\/(edges|products|demand)|scenarios|crosswalk)$/)) return json([])
    if (path.match(/\/onboarding\/runs$/)) return json([])
    if (path.match(/\/basemap\.geojson$/)) return json({ type: 'FeatureCollection', features: [] })
    if (path.match(/\/sessions$/)) return json({ current: null, sessions: [] })
    if (path.match(/\/scorecard$/)) return json({ detail: 'This country has no baseline scenario.' }, 404)
    if (path.match(/\/estimators$/)) return readFile('estimators.json').then(function (base) { var z = zeroed(base); z.rules = base.rules; return json(z) })
    if (path.match(/\/onboarding\/pack$/)) return file('onboarding-pack.json')
    if ((m = path.match(/\/season\/(\d+)$/))) return readFile('season/' + m[1] + '.json').then(function (base) { var z = zeroed(base); z.month = base.month; z.month_name = base.month_name; return json(z) })
    return undefined
  }

  var sandboxWrite = function (method, path, init) {
    var cid = countryIdOf(path)
    var m
    if (method === 'POST' && path === '/countries') {
      var body = bodyOf(init)
      var code = String(body.code || '').trim().toUpperCase()
      if (!code || !String(body.name || '').trim()) return json({ detail: 'A country needs a code and a name.' }, 422)
      return readFile('countries.json').then(function (base) {
        var clash = base.concat(sandbox.countries).filter(function (c) { return c.code === code })[0]
        if (clash) return json({ detail: code + " already exists as '" + clash.name + "'. Open that workspace, or choose another code." }, 409)
        var country = { id: sandbox.nextCountry++, code: code, name: String(body.name).trim(), currency: String(body.currency || 'USD').trim().toUpperCase(),
          config: { bbox: { min_lat: -90, max_lat: 90, min_lon: -180, max_lon: 180 }, level_labels: { 0: 'National store', 1: 'Regional store', 2: 'District store', 3: 'Facility' } },
          has_boundary: false }
        sandbox.countries.push(country)
        sandbox.nodes[country.id] = []
        auditRow(country.id, 'global', code, 'created', 'Workspace opened for ' + country.name + sandboxNote, authorOf(init), 'A new country workspace, in this browser.')
        persist()
        return json(country, 201)
      })
    }
    if (method === 'POST' && (m = path.match(/^\/countries\/(\d+)\/nodes$/)) && (cid === 1 || sandboxCountry(cid))) {
      var f = bodyOf(init)
      var lat = Number(f.lat), lon = Number(f.lon)
      if (!String(f.code || '').trim() || !String(f.name || '').trim()) return json({ detail: 'A facility needs a code and a name.' }, 422)
      if (!isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return json({ detail: 'Latitude must be between -90 and 90 and longitude between -180 and 180.' }, 422)
      return allNodes(cid).then(function (existing) {
        if (existing.some(function (n) { return n.code === String(f.code).trim() })) return json({ detail: 'A facility with code ' + String(f.code).trim() + ' already exists.' }, 409)
        var node = makeNode(cid, f)
        sandbox.nodes[cid] = sandboxNodes(cid).concat([node])
        auditRow(cid, 'node', node.code, 'created', node.name + ' at ' + node.lat.toFixed(4) + ', ' + node.lon.toFixed(4) + sandboxNote, authorOf(init), f.reason || '')
        persist()
        return json({ node: node, issues: [] }, 201)
      })
    }
    if (method === 'POST' && (m = path.match(/^\/countries\/(\d+)\/imports\/csv\/inspect$/)) && (cid === 1 || sandboxCountry(cid))) {
      return fileText(init).then(function (got) {
        var parsed = parseCsv(got.text)
        if (!parsed.header.length) return json({ detail: 'The first row has no column names.' }, 400)
        var guesses = {}, fields = {}
        Object.keys(OUR).forEach(function (sheet) {
          guesses[sheet] = guessMapping(parsed.header, sheet)
          fields[sheet] = OUR[sheet].map(function (x) { return { key: x[0], description: x[1], required: x[2] } })
        })
        return json({ filename: got.name, columns: parsed.header, row_count: parsed.rows.length,
          sample: parsed.rows.slice(0, 5).map(function (r) { var o = {}; Object.keys(r).forEach(function (k) { if (k !== '_row') o[k] = r[k] }); return o }),
          delimiter: parsed.delimiter, sheet: guessSheet(parsed.header), guesses: guesses, fields: fields, matched_saved: null, saved: [] })
      }, function () { return refuse('No file arrived.') })
    }
    if (method === 'POST' && (m = path.match(/^\/countries\/(\d+)\/imports\/csv$/)) && (cid === 1 || sandboxCountry(cid))) {
      return fileText(init).then(function (got) {
        var sheet = got.form.get('sheet') || 'Nodes'
        if (sheet !== 'Nodes') return refuse('This demo can take facility lists only. Lanes, products and demand need the validator on the server.')
        var mapping = {}
        try { mapping = JSON.parse(got.form.get('mapping') || '{}') } catch (e) { /* ignore */ }
        var parsed = parseCsv(got.text)
        var issues = [], records = []
        parsed.rows.forEach(function (r) {
          var rec = {}
          Object.keys(mapping).forEach(function (field) { if (mapping[field] && r[mapping[field]] !== undefined && r[mapping[field]] !== '') rec[field] = r[mapping[field]] })
          var problems = []
          if (!rec.code) problems.push(['missing_code', 'Row ' + r._row + ' has no facility code.', 'Map a column with a unique code.'])
          if (!rec.name) problems.push(['missing_name', 'Row ' + r._row + ' has no name.', 'Map the facility name column.'])
          var lat = Number(rec.lat), lon = Number(rec.lon)
          if (!isFinite(lat) || Math.abs(lat) > 90) problems.push(['bad_latitude', (rec.name || 'Row ' + r._row) + ': latitude ' + rec.lat + ' is not a decimal degree between -90 and 90.', 'Check the column mapping and the sign.'])
          if (!isFinite(lon) || Math.abs(lon) > 180) problems.push(['bad_longitude', (rec.name || 'Row ' + r._row) + ': longitude ' + rec.lon + ' is not a decimal degree between -180 and 180.', 'Check the column mapping.'])
          problems.forEach(function (p) { issues.push({ severity: 'error', code: p[0], message: p[1], suggestion: p[2], sheet: 'Nodes', row: r._row, entity: rec.code || '' }) })
          if (!problems.length) records.push(rec)
        })
        var blocking = issues.length > 0
        var batch = { id: sandbox.nextBatch++, cid: cid, records: records }
        sandbox.batches[batch.id] = batch
        persist()
        var errors = issues.length
        return json({ blocking: blocking, counts: { error: errors, warning: 0, info: 0 }, issues: issues,
          headline: blocking ? errors + ' problem' + (errors === 1 ? '' : 's') + ' must be fixed before this file can be applied.' : parsed.rows.length + ' rows read as Nodes. Review the changes below, then apply.',
          batch_id: batch.id, missing_sheets: [], stats: { nodes: records.length }, source: 'csv', sheet: 'Nodes', rows: parsed.rows.length })
      }, function () { return refuse('No file arrived.') })
    }
    if (method === 'GET' && (m = path.match(/^\/imports\/(\d+)\/changes$/)) && sandbox.batches[m[1]]) {
      var b = sandbox.batches[m[1]]
      return allNodes(b.cid).then(function (existing) {
        var byCode = {}
        existing.forEach(function (n) { byCode[n.code] = n })
        var adds = [], updates = [], unchanged = 0
        b.records.forEach(function (rec) {
          var row = { key: rec.code, label: rec.name || rec.code, kind: 'add', fields: {}, conflicts: {}, method: 'code' }
          var have = byCode[rec.code]
          if (!have) { adds.push(row); return }
          var changed = {}
          ;['name', 'lat', 'lon', 'admin1', 'admin2', 'catchment_population'].forEach(function (k) {
            if (rec[k] === undefined) return
            var to = (k === 'lat' || k === 'lon' || k === 'catchment_population') ? Number(rec[k]) : rec[k]
            if (String(have[k]) !== String(to)) changed[k] = { from: have[k], to: to }
          })
          if (Object.keys(changed).length) { row.kind = 'update'; row.fields = changed; updates.push(row) } else unchanged++
        })
        var empty = { counts: { add: 0, update: 0, retire: 0, restore: 0, conflict: 0, unchanged: 0 }, adds: [], updates: [], retires: [], restores: [], conflicts: [] }
        return json({ batch_id: b.id, mode: 'merge', headline: adds.length + ' new facilities, ' + updates.length + ' updated, ' + unchanged + ' unchanged. A CSV is one sheet, so it is merged: rows it does not mention are left alone, never retired.',
          conflicts: 0, collisions: [],
          nodes: { counts: { add: adds.length, update: updates.length, retire: 0, restore: 0, conflict: 0, unchanged: unchanged }, adds: adds, updates: updates, retires: [], restores: [], conflicts: [] },
          edges: empty, products: empty, demand: empty })
      })
    }
    if (method === 'POST' && (m = path.match(/^\/imports\/(\d+)\/commit/)) && sandbox.batches[m[1]]) {
      var bb = sandbox.batches[m[1]]
      return allNodes(bb.cid).then(function (existing) {
        var byCode = {}
        existing.forEach(function (n) { byCode[n.code] = n })
        var created = 0, updated = 0
        bb.records.forEach(function (rec) {
          var have = byCode[rec.code]
          if (have) {
            if (isSandboxNode(have.id)) {
              ;['name', 'lat', 'lon', 'admin1', 'admin2', 'catchment_population'].forEach(function (k) { if (rec[k] !== undefined) have[k] = (k === 'lat' || k === 'lon' || k === 'catchment_population') ? Number(rec[k]) : rec[k] })
              updated++
            }
            return
          }
          var node = makeNode(bb.cid, rec)
          sandbox.nodes[bb.cid] = sandboxNodes(bb.cid).concat([node])
          byCode[node.code] = node
          created++
        })
        auditRow(bb.cid, 'global', 'csv', 'csv_merge', created + ' facilities created, ' + updated + ' updated from a CSV' + sandboxNote, authorOf(init), 'Applied in this browser from a mapped CSV.')
        delete sandbox.batches[bb.id]
        persist()
        return json({ committed: true, mode: 'merge', counts: { nodes_created: created, nodes_updated: updated, nodes_retired: 0, nodes_restored: 0, edges: 0, demand: 0, demand_rows: 0, conflicts: { total: 0, kept: 0, took_file: 0 } } })
      })
    }
    return undefined
  }

  // A sandbox country's exports are built here, since no file was captured for it.
  var csvOf = function (nodes) {
    var cols = ['code', 'name', 'level', 'type', 'lat', 'lon', 'admin1', 'admin2', 'catchment_population', 'operating_status', 'hub_capable']
    var esc = function (v) { v = v == null ? '' : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v }
    return cols.join(',') + '\n' + nodes.map(function (n) { return cols.map(function (c) { return esc(c === 'hub_capable' ? (n[c] ? 'TRUE' : 'FALSE') : n[c]) }).join(',') }).join('\n') + '\n'
  }

  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : input.url
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase()

    if (url.indexOf(API) !== 0) return nativeFetch(input, init)
    var path = url.slice(API.length).split('?')[0]

    var sandboxed = method === 'GET' ? sandboxRead(path) : sandboxWrite(method, path, init)
    if (sandboxed === undefined && method === 'GET' && path.match(/^\/imports\/\d+\/changes$/)) sandboxed = sandboxWrite(method, path, init)
    if (sandboxed !== undefined) return Promise.resolve(sandboxed)

    // ---- reads -------------------------------------------------------------
    if (method === 'GET') {
      var m
      if (path === '/countries') return file('countries.json')
      if (path === '/connectors') return file('connectors.json')
      if ((m = path.match(/^\/countries\/\d+\/(overview|nodes|edges|audit|connections|products|estimators)$/)))
        return file(m[1] + '.json')
      if (path.match(/^\/countries\/\d+\/nodes\/retired$/)) return file('retired.json')
      if (path.match(/^\/countries\/\d+\/sessions$/)) return file('sessions.json')
      if (path.match(/^\/countries\/\d+\/onboarding\/pack$/)) return file('onboarding-pack.json')
      if (path.match(/^\/countries\/\d+\/onboarding\/runs$/)) return json([])
      if (path.match(/^\/countries\/\d+\/crosswalk/)) return json([])
      if (path.match(/^\/countries\/\d+\/studies$/)) return file('studies.json')
      if (path === '/study-presets') return file('study-presets.json')
      if (path.match(/^\/countries\/\d+\/column-mappings$/)) return file('column-mappings.json')
      if ((m = path.match(/^\/countries\/\d+\/tables\/(nodes|edges|products|demand)$/))) return file('tables-' + m[1] + '.json')
      if ((m = path.match(/^\/nodes\/(\d+)\/demand$/))) return file('demand/' + m[1] + '.json')
      if (path.match(/^\/countries\/\d+\/basemap\.geojson$/)) return file('basemap.json')
      if ((m = path.match(/^\/countries\/\d+\/season\/(\d+)$/))) return file('season/' + m[1] + '.json')
      if ((m = path.match(/^\/results\/(\d+)$/))) return file('results/' + m[1] + '.json')

      if (path.match(/^\/countries\/\d+\/scenarios$/))
        return snapshot().then(function (s) { return json(s.scenarios) })
      if (path.match(/^\/countries\/\d+\/scorecard$/))
        return snapshot().then(function (s) { return json(s.scorecard) })

      if ((m = path.match(/^\/scenarios\/(\d+)\/roadmap$/))) {
        return readFile('roadmap/' + m[1] + '.json').then(function (body) {
          // The baseline has no roadmap by design; reproduce the real refusal.
          if (body && body.__status) return json({ detail: body.detail }, body.__status)
          return json(body)
        })
      }
      return refuse('This demo only carries the parts of the tool you can see.')
    }

    // ---- moving to a month that was solved ahead of time --------------------
    if (method === 'PATCH' && (m = path.match(/^\/scenarios\/(\d+)$/))) {
      var sid = Number(m[1])
      var patch = {}
      try { patch = JSON.parse((init && init.body) || '{}') } catch (e) { /* ignore */ }
      if (!patch.levers || !('month' in patch.levers)) {
        return refuse('Editing levers needs the solver. This demo carries the scenarios as they were run.')
      }
      state.key = keyFor(sid, patch.levers.month)
      return snapshot().then(function (snap) {
        var found = snap.scenarios.filter(function (s) { return s.id === sid })[0]
        return found ? json(found) : refuse('That scenario is not part of this demo.')
      })
    }

    if (method === 'POST' && (m = path.match(/^\/scenarios\/(\d+)\/run$/))) {
      var rid = Number(m[1])
      // A plain Run, with no month change, shows that scenario as it was solved.
      if (state.key.indexOf(rid + '-') !== 0) state.key = keyFor(rid, state.months[rid])
      return json({ id: rid, status: 'ok' })
    }

    if (method === 'POST' && path.match(/^\/countries\/\d+\/run-set$/)) {
      var ids = []
      try { ids = JSON.parse((init && init.body) || '[]') } catch (e) { /* ignore */ }
      return json({ ran: ids.length })
    }

    if (path.match(/\/estimates\/(preview|apply|recompute)$/))
      return refuse('Estimating needs the rules on the server. Download the tool to fill blank rows.')
    if (path.match(/\/greenfield/))
      return refuse('Placing stores needs the server. Download the tool to ask where the next store should go.')
    if (path.match(/\/studies(\/|$)/) || path.match(/\/diff-map\//))
      return refuse('Studies need the solver. Download the tool to ask a question of your own network.')
    if (path.match(/\/sessions(\/|$)/))
      return refuse('Saving and opening sessions needs the server. Download the tool to keep your work.')
    // ---- everything that would change the model -----------------------------
    if (path.match(/\/imports\/csv/))
      return refuse('Mapping a CSV needs the validator, which runs on the server. Download the tool to try it.')
    if (path.indexOf('/onboarding') !== -1)
      return refuse('Onboarding a ministry\'s files needs the server: the pipeline, the reconciler and the sign-off all run there. Download the tool to try it.')
    if (path.match(/\/validate$/) || path.match(/^\/imports\//))
      return refuse('Uploading a workbook needs the validator, which runs on the server. Download the tool to try it.')
    if (path.match(/^\/connections/) || path.match(/\/connections$/))
      return refuse('Connecting to DHIS2, OpenLMIS or mSupply needs a live server. Download the tool to try it.')
    return refuse('This demo keeps only what you add in this browser: a country, facilities, a facility CSV. Everything else that changes the model needs the server. Download the tool to try it.')
  }

  // Export and report links are plain hrefs the interface builds as /api/..., so point
  // them at the files captured beside this page. The href itself is rewritten, not just
  // the click: a middle-click, a right-click "open in new tab" or a copied link all have
  // to land on the file, and a root-absolute /api/... on GitHub Pages lands on nothing.
  var EXPORTS = [
    [/^\/api\/template\.xlsx$/, 'files/template.xlsx'],
    [/^\/api\/countries\/\d+\/export\/network\.xlsx$/, 'files/network.xlsx'],
    [/^\/api\/scenarios\/(\d+)\/export\/results\.xlsx$/, 'files/results-$1.xlsx'],
    [/^\/api\/scenarios\/(\d+)\/report\.html$/, 'files/report-$1.html'],
    [/^\/api\/studies\/(\d+)\/report\.html$/, 'files/study-report-$1.html'],
    [/^\/api\/countries\/\d+\/crosswalk\.csv$/, 'files/crosswalk.csv'],
    [/^\/api\/countries\/\d+\/export\/csv\.zip$/, 'files/network-csv.zip'],
    [/^\/api\/countries\/\d+\/export\/(nodes|edges|products|demand)\.csv$/, 'files/$1.csv'],
    [/^\/api\/onboarding\/runs\/\d+\/export\.xlsx$/, null],
  ]
  var captured = function (href) {
    for (var i = 0; i < EXPORTS.length; i++) {
      var hit = href.match(EXPORTS[i][0])
      if (hit) return EXPORTS[i][1] ? ROOT + EXPORTS[i][1].replace('$1', hit[1]) : null
    }
    return undefined
  }
  var SANDBOX_EXPORT = /^\/api\/countries\/(\d+)\/export\/(network\.xlsx|csv\.zip|nodes\.csv|edges\.csv|products\.csv|demand\.csv)$/
  var sandboxExport = function (href) {
    var m = (href || '').match(SANDBOX_EXPORT)
    return m && sandboxCountry(Number(m[1])) ? m : null
  }
  var downloadSandbox = function (href) {
    var m = sandboxExport(href)
    if (!m) return
    var nodes = sandboxNodes(Number(m[1]))
    var text = m[2] === 'nodes.csv' || m[2] === 'csv.zip' || m[2] === 'network.xlsx' ? csvOf(nodes) : 'code\n'
    var url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
    var a = document.createElement('a')
    a.href = url
    a.download = 'facilities.csv'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(function () { URL.revokeObjectURL(url) }, 5000)
  }
  var rewrite = function (root) {
    // The added node is often the anchor itself, which querySelectorAll never returns.
    var links = root.matches && root.matches('a[href^="/api/"]') ? [root] : []
    if (root.querySelectorAll) links = links.concat(Array.prototype.slice.call(root.querySelectorAll('a[href^="/api/"]')))
    for (var i = 0; i < links.length; i++) {
      var a = links[i]
      if (sandboxExport(a.getAttribute('href'))) {
        a.setAttribute('data-demo-api', a.getAttribute('href'))
        a.setAttribute('data-demo-sandbox', '1')
        a.setAttribute('href', '#facilities.csv')
        a.setAttribute('title', 'Facilities you added in this browser, as CSV. The workbook needs the server.')
        continue
      }
      var target = captured(a.getAttribute('href'))
      if (target) {
        a.setAttribute('data-demo-api', a.getAttribute('href'))
        a.setAttribute('href', target)
      } else if (target === null) {
        a.setAttribute('aria-disabled', 'true')
        a.setAttribute('title', 'Not part of this read-only demo. Download the tool to export it.')
        a.addEventListener('click', function (event) { event.preventDefault() })
      }
    }
  }
  rewrite(document)
  new MutationObserver(function (records) {
    for (var i = 0; i < records.length; i++) {
      var added = records[i].addedNodes
      for (var j = 0; j < added.length; j++) if (added[j].nodeType === 1) rewrite(added[j])
      if (records[i].type === 'attributes' && records[i].target.matches && records[i].target.matches('a[href^="/api/"]')) rewrite(records[i].target.parentNode || document)
    }
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['href'] })
  // And the click, for a link rendered between two observer ticks.
  document.addEventListener('click', function (event) {
    var own = event.target && event.target.closest && event.target.closest('a[data-demo-sandbox]')
    if (own) {
      event.preventDefault()
      downloadSandbox(own.getAttribute('data-demo-api'))
      return
    }
    var a = event.target && event.target.closest && event.target.closest('a[href^="/api/"]')
    if (!a) return
    var target = captured(a.getAttribute('href'))
    if (target === undefined) return
    event.preventDefault()
    if (!target) return
    if (a.getAttribute('target') === '_blank') window.open(target, '_blank', 'noopener')
    else window.location.href = target
  }, true)
})()
