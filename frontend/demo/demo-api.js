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

  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : input.url
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase()

    if (url.indexOf(API) !== 0) return nativeFetch(input, init)
    var path = url.slice(API.length).split('?')[0]

    // ---- reads -------------------------------------------------------------
    if (method === 'GET') {
      var m
      if (path === '/countries') return file('countries.json')
      if (path === '/connectors') return file('connectors.json')
      if ((m = path.match(/^\/countries\/\d+\/(overview|nodes|edges|audit|connections)$/)))
        return file(m[1] + '.json')
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

    // ---- everything that would change the model -----------------------------
    if (path.match(/\/validate$/) || path.match(/^\/imports\//))
      return refuse('Uploading a workbook needs the validator, which runs on the server. Download the tool to try it.')
    if (path.match(/^\/connections/) || path.match(/\/connections$/))
      return refuse('Connecting to DHIS2, OpenLMIS or mSupply needs a live server. Download the tool to try it.')
    return refuse('This is a read-only demo. Download the tool to change anything.')
  }

  // Export links are plain hrefs the interface builds as /api/..., so point them
  // at the files captured beside this page.
  var EXPORTS = [
    [/^\/api\/template\.xlsx$/, 'files/template.xlsx'],
    [/^\/api\/countries\/\d+\/export\/network\.xlsx$/, 'files/network.xlsx'],
    [/^\/api\/scenarios\/(\d+)\/export\/results\.xlsx$/, 'files/results-$1.xlsx'],
  ]
  document.addEventListener('click', function (event) {
    var a = event.target && event.target.closest && event.target.closest('a[href^="/api/"]')
    if (!a) return
    for (var i = 0; i < EXPORTS.length; i++) {
      var hit = a.getAttribute('href').match(EXPORTS[i][0])
      if (hit) {
        event.preventDefault()
        window.location.href = ROOT + EXPORTS[i][1].replace('$1', hit[1])
        return
      }
    }
  }, true)
})()
