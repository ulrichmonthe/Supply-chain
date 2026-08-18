import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { EdgeRow, NodeRow, Result, SeasonView } from '../types'
import { exact, fillColour, frequencyLabel, modeColour, pct, riskColour } from '../format'

export type ColourBy = 'fill' | 'risk' | 'vulnerability' | 'mode'

type Props = {
  countryId: number
  nodes: NodeRow[]
  edges: EdgeRow[]
  basemap: GeoJSON.FeatureCollection | null
  result: Result | null
  season: SeasonView | null
  month: number | null
  colourBy: ColourBy
  onColourBy: (value: ColourBy) => void
  selectedCode: string | null
  onSelect: (code: string | null) => void
  center?: { lat: number; lon: number; zoom: number }
}

type Hover = { x: number; y: number; html: ReactNode } | null

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

export function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const [ready, setReady] = useState(false)
  const [hover, setHover] = useState<Hover>(null)
  const [online, setOnline] = useState(false)
  const mapId = useId()
  const [tilesUnavailable, setTilesUnavailable] = useState(false)

  const nodeByCode = useMemo(() => new Map(props.nodes.map((n) => [n.code, n])), [props.nodes])
  const detailByCode = useMemo(
    () => new Map((props.result?.per_node_detail ?? []).map((d) => [d.code, d])),
    [props.result],
  )
  const flowByEdge = useMemo(
    () => new Map((props.result?.per_edge_flow ?? []).map((f) => [f.edge_id, f])),
    [props.result],
  )
  const closedEdges = useMemo(
    () => new Set((props.season?.closed_lanes ?? []).map((l) => l.edge_code)),
    [props.season],
  )
  const degradedEdges = useMemo(
    () => new Set((props.season?.degraded_lanes ?? []).map((l) => l.edge_code)),
    [props.season],
  )

  // --- lane geometry ---------------------------------------------------------------
  // Lanes are drawn as gentle arcs rather than straight lines. On a map with several
  // hundred overlapping hub-to-facility legs, straight lines converge into an
  // unreadable star; an arc lets you see that two hubs both reach the same island.
  const laneData = useMemo<GeoJSON.FeatureCollection>(() => {
    if (!props.edges.length) return EMPTY
    return {
      type: 'FeatureCollection',
      features: props.edges
        .filter((edge) => edge.active)
        .map((edge) => {
          const flow = flowByEdge.get(edge.id)
          const closed = closedEdges.has(edge.code)
          const degraded = degradedEdges.has(edge.code)
          return {
            type: 'Feature' as const,
            properties: {
              code: edge.code,
              mode: edge.mode,
              colour: closed ? '#ef6b6b' : modeColour(edge.mode),
              used: flow ? 1 : 0,
              volume: flow?.volume_m3 ?? 0,
              width: flow ? Math.min(4.2, 0.6 + Math.sqrt(flow.volume_m3) * 0.22) : 0.7,
              opacity: closed ? 0.85 : flow ? 0.72 : degraded ? 0.3 : 0.14,
              dashed: closed ? 1 : 0,
              scheduled: edge.service_frequency && edge.mode !== 'road' ? 1 : 0,
            },
            geometry: { type: 'LineString' as const, coordinates: arc(edge) },
          }
        }),
    }
  }, [props.edges, flowByEdge, closedEdges, degradedEdges])

  const nodeData = useMemo<GeoJSON.FeatureCollection>(() => {
    const cutOff = new Set((props.season?.facilities_cut_off ?? []).map((f) => f.code))
    const lostSurface = new Set((props.season?.facilities_losing_surface_access ?? []).map((f) => f.code))
    return {
      type: 'FeatureCollection',
      features: props.nodes.map((node) => {
        const detail = detailByCode.get(node.code)
        let colour = '#4a5c72'
        if (props.colourBy === 'fill') colour = detail ? fillColour(detail.fill_rate) : '#4a5c72'
        else if (props.colourBy === 'risk') colour = detail ? riskColour(detail.stockout_risk) : '#4a5c72'
        else if (props.colourBy === 'vulnerability')
          colour = detail ? vulnerabilityColour(detail.vulnerability) : '#4a5c72'
        else colour = detail?.primary_mode ? modeColour(detail.primary_mode) : '#4a5c72'

        const isHub = node.hub_capable
        const isCentral = node.level === 0
        return {
          type: 'Feature' as const,
          properties: {
            code: node.code,
            name: node.name,
            level: node.level,
            hub: isHub || isCentral ? 1 : 0,
            open: node.operating_status === 'operational' ? 1 : 0,
            colour: isCentral ? '#ffffff' : isHub ? '#4da3ff' : colour,
            radius: isCentral ? 8 : isHub ? 6.5 : 3 + Math.min(4, Math.sqrt(node.catchment_population) / 130),
            cutOff: cutOff.has(node.code) ? 1 : 0,
            lostSurface: lostSurface.has(node.code) ? 1 : 0,
            selected: props.selectedCode === node.code ? 1 : 0,
          },
          geometry: { type: 'Point' as const, coordinates: [node.lon, node.lat] },
        }
      }),
    }
  }, [props.nodes, detailByCode, props.colourBy, props.season, props.selectedCode])

  // --- map lifecycle ------------------------------------------------------------------
  useEffect(() => {
    if (!container.current || map.current) return
    const centre = props.center ?? { lat: -6.4, lon: 147.5, zoom: 5.2 }
    const instance = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        sources: {},
        layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0a1119' } }],
      },
      center: [centre.lon, centre.lat],
      zoom: centre.zoom,
      attributionControl: false,
    })
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    instance.on('load', () => {
      // The coarse land mask is always drawn, and the online basemap sits on top of
      // it. If the tiles never arrive -- which is the normal case in a provincial
      // office in Papua New Guinea -- the map degrades to the offline geometry
      // rather than to a blank screen.
      instance.addSource('land', { type: 'geojson', data: EMPTY })
      instance.addLayer({
        id: 'land-fill',
        type: 'fill',
        source: 'land',
        paint: { 'fill-color': '#1e2b39', 'fill-opacity': 1 },
      })
      instance.addLayer({
        id: 'land-line',
        type: 'line',
        source: 'land',
        paint: { 'line-color': '#3c5372', 'line-width': 1.1 },
      })

      instance.addSource('osm', {
        type: 'raster',
        tiles: ['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
        tileSize: 256,
        maxzoom: 18,
        attribution: '© OpenStreetMap contributors © CARTO',
      })
      instance.addLayer({
        id: 'osm',
        type: 'raster',
        source: 'osm',
        layout: { visibility: 'none' },
        paint: { 'raster-opacity': 0.85 },
      })

      instance.addSource('lanes', { type: 'geojson', data: EMPTY })
      instance.addLayer({
        id: 'lanes',
        type: 'line',
        source: 'lanes',
        layout: { 'line-cap': 'round' },
        paint: {
          'line-color': ['get', 'colour'],
          'line-width': ['get', 'width'],
          'line-opacity': ['get', 'opacity'],
          'line-dasharray': [
            'case',
            ['==', ['get', 'dashed'], 1],
            ['literal', [1, 1.6]],
            ['literal', [1, 0]],
          ],
        },
      })

      instance.addSource('nodes', { type: 'geojson', data: EMPTY })
      instance.addLayer({
        id: 'node-halo',
        type: 'circle',
        source: 'nodes',
        filter: ['any', ['==', ['get', 'cutOff'], 1], ['==', ['get', 'selected'], 1]],
        paint: {
          'circle-radius': ['+', ['get', 'radius'], 5],
          'circle-color': 'transparent',
          'circle-stroke-width': 1.6,
          'circle-stroke-color': [
            'case',
            ['==', ['get', 'cutOff'], 1],
            '#ef6b6b',
            '#ffffff',
          ],
        },
      })
      instance.addLayer({
        id: 'nodes',
        type: 'circle',
        source: 'nodes',
        paint: {
          'circle-radius': ['get', 'radius'],
          'circle-color': ['get', 'colour'],
          'circle-stroke-width': ['case', ['==', ['get', 'hub'], 1], 1.6, 0.7],
          'circle-stroke-color': [
            'case',
            ['==', ['get', 'lostSurface'], 1],
            '#e8955a',
            ['==', ['get', 'open'], 0],
            '#8fa3b8',
            'rgba(6,12,18,0.85)',
          ],
        },
      })
      instance.addLayer({
        id: 'hub-labels',
        type: 'symbol',
        source: 'nodes',
        filter: ['==', ['get', 'hub'], 1],
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 10.5,
          'text-offset': [0, 1.4],
          'text-anchor': 'top',
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#cfe0f2',
          'text-halo-color': '#0a1119',
          'text-halo-width': 1.4,
        },
      })
      setReady(true)
    })

    // If the tile server cannot be reached — the normal case in a provincial office,
    // and the case here whenever the machine is offline — stop pretending and go back
    // to the offline mask rather than leaving the user with a faded, empty map.
    instance.on('error', (event: { sourceId?: string }) => {
      if (event?.sourceId === 'osm') {
        setTilesUnavailable(true)
        setOnline(false)
      }
    })

    map.current = instance
    return () => {
      instance.remove()
      map.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!ready || !map.current) return
    ;(map.current.getSource('land') as maplibregl.GeoJSONSource | undefined)?.setData(props.basemap ?? EMPTY)
  }, [ready, props.basemap])

  useEffect(() => {
    if (!ready || !map.current?.getLayer('osm')) return
    map.current.setLayoutProperty('osm', 'visibility', online ? 'visible' : 'none')
    map.current.setPaintProperty('land-fill', 'fill-opacity', online ? 0.12 : 1)
    map.current.setPaintProperty('land-line', 'line-opacity', online ? 0.25 : 1)
  }, [ready, online])

  useEffect(() => {
    if (!ready || !map.current) return
    ;(map.current.getSource('lanes') as maplibregl.GeoJSONSource | undefined)?.setData(laneData)
  }, [ready, laneData])

  useEffect(() => {
    if (!ready || !map.current) return
    ;(map.current.getSource('nodes') as maplibregl.GeoJSONSource | undefined)?.setData(nodeData)
  }, [ready, nodeData])

  // Frame the country once, when the facilities first arrive. Doing it on every
  // update would fight the user every time they panned.
  const framed = useRef(false)
  useEffect(() => {
    if (!ready || !map.current || framed.current || !props.nodes.length) return
    const bounds = new maplibregl.LngLatBounds(
      [props.nodes[0].lon, props.nodes[0].lat],
      [props.nodes[0].lon, props.nodes[0].lat],
    )
    for (const node of props.nodes) bounds.extend([node.lon, node.lat])
    map.current.fitBounds(bounds, { padding: { top: 70, bottom: 60, left: 60, right: 60 }, duration: 0 })
    framed.current = true
  }, [ready, props.nodes])

  // --- interaction ------------------------------------------------------------------
  useEffect(() => {
    const instance = map.current
    if (!ready || !instance) return

    const onMove = (event: maplibregl.MapMouseEvent) => {
      const hits = instance.queryRenderedFeatures(event.point, { layers: ['nodes', 'lanes'] })
      if (!hits.length) {
        setHover(null)
        instance.getCanvas().style.cursor = ''
        return
      }
      instance.getCanvas().style.cursor = 'pointer'
      const hit = hits[0]
      const html =
        hit.layer.id === 'nodes'
          ? nodeTooltip(String(hit.properties?.code))
          : laneTooltip(String(hit.properties?.code))
      if (html) setHover({ x: event.point.x, y: event.point.y, html })
    }

    const onClick = (event: maplibregl.MapMouseEvent) => {
      const hits = instance.queryRenderedFeatures(event.point, { layers: ['nodes'] })
      props.onSelect(hits.length ? String(hits[0].properties?.code) : null)
    }

    instance.on('mousemove', onMove)
    instance.on('click', onClick)
    instance.on('mouseout', () => setHover(null))
    return () => {
      instance.off('mousemove', onMove)
      instance.off('click', onClick)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, nodeByCode, detailByCode, props.edges, flowByEdge, props.month])

  function nodeTooltip(code: string) {
    const node = nodeByCode.get(code)
    if (!node) return null
    const detail = detailByCode.get(code)
    return (
      <>
        <b>{node.name}</b>
        <div className="dim tiny" style={{ marginBottom: 5 }}>
          {node.admin1 ?? '—'} · {node.terrain_class.replace(/_/g, ' ')} ·{' '}
          {exact(node.catchment_population)} people
        </div>
        {detail ? (
          <>
            <div className="kv">
              <span>Fill rate</span>
              <span style={{ color: fillColour(detail.fill_rate) }}>{pct(detail.fill_rate)}</span>
            </div>
            <div className="kv">
              <span>Stockout risk</span>
              <span style={{ color: riskColour(detail.stockout_risk) }}>{pct(detail.stockout_risk)}</span>
            </div>
            <div className="kv">
              <span>Supplied by</span>
              <span>{detail.served_by[0]?.hub_name ?? 'nothing'}</span>
            </div>
            <div className="kv">
              <span>Service</span>
              <span>
                {detail.service_name ?? detail.primary_mode ?? '—'} ·{' '}
                {frequencyLabel(detail.service_frequency)}
              </span>
            </div>
            <div className="kv">
              <span>Storage</span>
              <span>
                {detail.storage_days.toFixed(0)} days{detail.storage_binding ? ' (binding)' : ''}
              </span>
            </div>
          </>
        ) : (
          <div className="dim tiny">Run a scenario to see how this facility is supplied.</div>
        )}
      </>
    )
  }

  function laneTooltip(code: string) {
    const edge = props.edges.find((e) => e.code === code)
    if (!edge) return null
    const flow = flowByEdge.get(edge.id)
    const access = props.month ? edge.monthly_access[props.month - 1] : null
    return (
      <>
        <b>{edge.service_name ?? edge.code}</b>
        <div className="dim tiny" style={{ marginBottom: 5 }}>
          {edge.from_code} → {edge.to_code} · {edge.mode} · {frequencyLabel(edge.service_frequency)}
          {edge.service_days?.length ? ` (${edge.service_days.join(', ')})` : ''}
        </div>
        <div className="kv">
          <span>Distance</span>
          <span>
            {edge.distance_km.toFixed(0)} km · {edge.distance_method.replace(/_/g, ' ')}
          </span>
        </div>
        {access !== null && (
          <div className="kv">
            <span>Access this month</span>
            <span style={{ color: access <= 0 ? '#ef6b6b' : access < 0.6 ? '#e8b23a' : '#3fb984' }}>
              {pct(access, 0)}
            </span>
          </div>
        )}
        {flow ? (
          <>
            <div className="kv">
              <span>Volume</span>
              <span>{flow.volume_m3.toFixed(1)} m³/yr</span>
            </div>
            <div className="kv">
              <span>Unit cost</span>
              <span>{flow.unit_cost.toFixed(0)} /m³</span>
            </div>
            {flow.capacity_utilisation !== null && (
              <div className="kv">
                <span>Hold used</span>
                <span>{pct(flow.capacity_utilisation, 0)}</span>
              </div>
            )}
          </>
        ) : (
          <div className="dim tiny">Not used in this scenario.</div>
        )}
      </>
    )
  }

  return (
    <div className="map-wrap">
      {/* The canvas itself carries no text. The map is named here, and the
          facility table under the Facilities tab is the accessible equivalent of
          what it shows — the numbers behind every dot, in a real table. */}
      <div
        className="map"
        ref={container}
        role="region"
        aria-label="Network map. The Facilities tab lists the same data as a table."
      />

      <div className="map-overlay map-legend">
        <h4>Lanes</h4>
        {['road', 'sea', 'air', 'river'].map((mode) => (
          <div className="legend-row" key={mode}>
            <span aria-hidden="true" className="legend-swatch" style={{ background: modeColour(mode) }} />
            {mode}
          </div>
        ))}
        {props.month && (
          <div className="legend-row" style={{ marginTop: 4 }}>
            <span
              aria-hidden="true"
              className="legend-swatch"
              style={{ background: 'repeating-linear-gradient(90deg,#ef6b6b 0 3px,transparent 3px 6px)' }}
            />
            closed this month
          </div>
        )}
        <h4 style={{ marginTop: 9 }}>Facilities</h4>
        <div className="legend-row">
          <span aria-hidden="true" className="legend-dot" style={{ background: '#ffffff' }} /> national store
        </div>
        <div className="legend-row">
          <span aria-hidden="true" className="legend-dot" style={{ background: '#4da3ff' }} /> area medical store
        </div>
        <div className="legend-row">
          <span aria-hidden="true" className="legend-dot" style={{ background: colourScaleHint(props.colourBy) }} />
          {colourByLabel(props.colourBy)}
        </div>
      </div>

      <div className="map-overlay map-mode">
        <label htmlFor={`${mapId}-colour-by`}>Colour facilities by</label>
        <select
          id={`${mapId}-colour-by`}
          value={props.colourBy}
          onChange={(event) => props.onColourBy(event.target.value as ColourBy)}
          style={{
            background: '#212d3d',
            border: '1px solid #2a3849',
            borderRadius: 5,
            padding: '4px 6px',
          }}
        >
          <option value="fill">Fill rate</option>
          <option value="risk">Stockout risk</option>
          <option value="vulnerability">Vulnerability</option>
          <option value="mode">Transport mode</option>
        </select>

        <span className="field-label" style={{ marginTop: 4 }}>
          Basemap
        </span>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={online}
            disabled={tilesUnavailable}
            onChange={(event) => setOnline(event.target.checked)}
          />
          Online tiles
        </label>
        <div className="tiny dim" style={{ maxWidth: 150, lineHeight: 1.3 }}>
          {tilesUnavailable
            ? 'Tile server unreachable — showing the offline land mask.'
            : online
              ? '© OpenStreetMap contributors © CARTO'
              : 'Offline coarse land mask — the same geometry the validator screens coordinates against.'}
        </div>
      </div>

      {hover && (
        <div
          className="map-tooltip"
          role="tooltip"
          style={{
            left: Math.min(hover.x + 14, (container.current?.clientWidth ?? 800) - 300),
            top: Math.min(hover.y + 14, (container.current?.clientHeight ?? 600) - 190),
          }}
        >
          {hover.html}
        </div>
      )}
    </div>
  )
}

/** A shallow great-circle-ish arc, bowed perpendicular to the chord. */
function arc(edge: EdgeRow): [number, number][] {
  const [x1, y1] = [edge.from_lon, edge.from_lat]
  const [x2, y2] = [edge.to_lon, edge.to_lat]
  const dx = x2 - x1
  const dy = y2 - y1
  const bow = edge.mode === 'road' ? 0.06 : 0.13
  const points: [number, number][] = []
  for (let i = 0; i <= 20; i += 1) {
    const t = i / 20
    const lift = Math.sin(Math.PI * t) * bow
    points.push([x1 + dx * t - dy * lift, y1 + dy * t + dx * lift])
  }
  return points
}

function vulnerabilityColour(score: number): string {
  if (score >= 0.7) return '#7c4dbd'
  if (score >= 0.5) return '#a06fd6'
  if (score >= 0.3) return '#c79ae8'
  return '#e0cbf2'
}

function colourByLabel(mode: ColourBy): string {
  return {
    fill: 'fill rate (green = supplied)',
    risk: 'stockout risk (red = high)',
    vulnerability: 'vulnerability (dark = most)',
    mode: 'transport mode',
  }[mode]
}

function colourScaleHint(mode: ColourBy): string {
  return { fill: '#3fb984', risk: '#ef6b6b', vulnerability: '#7c4dbd', mode: '#46c5b6' }[mode]
}
