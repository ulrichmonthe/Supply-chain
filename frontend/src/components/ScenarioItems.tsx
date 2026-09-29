import { useId, useState } from 'react'
import type { DataItem, EdgeRow, NodeRow, Scenario } from '../types'

/**
 * Data changes inside a scenario.
 *
 * A what-if is not only a lever. "Close Wewak", "demand up a fifth in Morobe",
 * "open a store at Kimbe with a road lane to each of these clinics" are changes to
 * the data, and a scenario carries them as items applied on top of the baseline when
 * it runs. The base data is never touched, and the study's lever diff lists the items
 * beside the levers.
 */

const KINDS: { kind: DataItem['kind']; label: string }[] = [
  { kind: 'close_facility', label: 'Close a facility' },
  { kind: 'set_node_field', label: 'Change a facility field' },
  { kind: 'scale_demand', label: 'Scale demand' },
  { kind: 'add_node', label: 'Open a new store' },
  { kind: 'add_lane', label: 'Add a lane' },
  { kind: 'remove_lane', label: 'Remove a lane' },
  { kind: 'set_lane_field', label: 'Change a lane field' },
]

const NODE_FIELDS = [
  'operating_status',
  'hub_capable',
  'catchment_population',
  'hub_fixed_cost',
  'hub_open_capex',
  'hub_throughput_m3',
  'level',
  'type',
  'terrain_class',
]
const LANE_FIELDS = [
  'mode',
  'cost_per_m3',
  'capacity_per_trip_m3',
  'service_frequency',
  'distance_km',
  'reliability',
  'fixed_cost_per_trip',
  'variable_cost_per_km',
  'active',
]

export function describeItem(item: DataItem, names: Map<string, string>): string {
  const who = (code?: string) => (code ? (names.get(code) ?? code) : '')
  switch (item.kind) {
    case 'close_facility':
      return `close ${who(item.code)}`
    case 'set_node_field':
      return `${who(item.code)}: ${String(item.field).replace(/_/g, ' ')} → ${String(item.value)}`
    case 'scale_demand':
      return `demand × ${item.factor}${item.admin1 ? ` in ${item.admin1}` : ''}${item.sku ? ` for ${item.sku}` : ''}`
    case 'add_node':
      return `open store ${item.name ?? item.code}${item.lat !== undefined ? ` at ${Number(item.lat).toFixed(2)}, ${Number(item.lon).toFixed(2)}` : ''}`
    case 'add_lane':
      return `add lane ${who(item.from_code)} → ${who(item.to_code)} by ${item.mode ?? 'road'}`
    case 'remove_lane':
      return `remove lane ${item.code}`
    case 'set_lane_field':
      return `lane ${item.code}: ${String(item.field).replace(/_/g, ' ')} → ${String(item.value)}`
    default:
      return item.kind
  }
}

function coerce(text: string): unknown {
  const trimmed = text.trim()
  if (/^(true|false|yes|no)$/i.test(trimmed)) return /^(true|yes)$/i.test(trimmed)
  const n = Number(trimmed.replace(/,/g, ''))
  return trimmed !== '' && !Number.isNaN(n) ? n : trimmed
}

export function ScenarioItems({
  scenario,
  nodes,
  edges,
  onPatch,
}: {
  scenario: Scenario
  nodes: NodeRow[]
  edges: EdgeRow[]
  onPatch: (id: number, patch: Partial<Scenario>) => void
}) {
  const [kind, setKind] = useState<DataItem['kind']>('close_facility')
  const [draft, setDraft] = useState<Record<string, string>>({})
  const ids = useId()
  const items = scenario.data_items ?? []
  const names = new Map(nodes.map((n) => [n.code, n.name]))
  const set = (key: string, value: string) => setDraft((d) => ({ ...d, [key]: value }))

  function build(): DataItem | null {
    switch (kind) {
      case 'close_facility':
        return draft.code ? { kind, code: draft.code } : null
      case 'set_node_field':
        return draft.code && draft.field && draft.value !== undefined
          ? { kind, code: draft.code, field: draft.field, value: coerce(draft.value ?? '') }
          : null
      case 'scale_demand':
        return draft.factor
          ? {
              kind,
              factor: Number(draft.factor),
              ...(draft.admin1 ? { admin1: draft.admin1 } : {}),
              ...(draft.sku ? { sku: draft.sku } : {}),
            }
          : null
      case 'add_node':
        return draft.code && draft.lat && draft.lon
          ? {
              kind,
              code: draft.code.trim(),
              name: draft.name || draft.code,
              lat: Number(draft.lat),
              lon: Number(draft.lon),
              level: 1,
              hub_capable: true,
              operating_status: 'planned',
              hub_fixed_cost: Number(draft.hub_fixed_cost || 0),
              hub_open_capex: Number(draft.hub_open_capex || 0),
              hub_throughput_m3: Number(draft.hub_throughput_m3 || 0),
            }
          : null
      case 'add_lane':
        return draft.from_code && draft.to_code
          ? {
              kind,
              code: draft.code || `${draft.from_code}-${draft.to_code}-${draft.mode || 'road'}`,
              from_code: draft.from_code,
              to_code: draft.to_code,
              mode: draft.mode || 'road',
              ...(draft.cost_per_m3 ? { cost_per_m3: Number(draft.cost_per_m3) } : {}),
              ...(draft.capacity_per_trip_m3
                ? { capacity_per_trip_m3: Number(draft.capacity_per_trip_m3) }
                : {}),
            }
          : null
      case 'remove_lane':
        return draft.code ? { kind, code: draft.code } : null
      case 'set_lane_field':
        return draft.code && draft.field
          ? { kind, code: draft.code, field: draft.field, value: coerce(draft.value ?? '') }
          : null
      default:
        return null
    }
  }

  const candidate = build()
  const addedCodes = items.filter((i) => i.kind === 'add_node').map((i) => i.code as string)
  const facilityCodes = [...nodes.map((n) => n.code), ...addedCodes]

  return (
    <div className="section" data-tour="scenario-items">
      <h3>Data changes in this scenario</h3>
      <p className="lever-note" style={{ marginBottom: 8 }}>
        Applied on top of today's data when this scenario runs; the data itself is never touched. They appear
        in the study's list of what differs.
      </p>
      {items.length > 0 && (
        <ul className="item-list" aria-label="Data changes">
          {items.map((item, index) => (
            <li key={index} className="item-row">
              <span>{describeItem(item, names)}</span>
              <button
                type="button"
                className="btn small ghost"
                aria-label={`Remove: ${describeItem(item, names)}`}
                onClick={() => onPatch(scenario.id, { data_items: items.filter((_, i) => i !== index) })}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <datalist id={`${ids}-nodes`}>
        {facilityCodes.map((code) => (
          <option key={code} value={code}>
            {names.get(code) ?? code}
          </option>
        ))}
      </datalist>
      <datalist id={`${ids}-lanes`}>
        {edges.map((e) => (
          <option key={e.code} value={e.code}>
            {e.from_code} → {e.to_code} ({e.mode})
          </option>
        ))}
      </datalist>
      <form
        className="session-form"
        onSubmit={(event) => {
          event.preventDefault()
          if (!candidate) return
          onPatch(scenario.id, { data_items: [...items, candidate] })
          setDraft({})
        }}
      >
        <label htmlFor={`${ids}-kind`} className="tiny dim">
          Add a change
        </label>
        <select
          id={`${ids}-kind`}
          className="tag-input"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as DataItem['kind'])
            setDraft({})
          }}
        >
          {KINDS.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>

        {(kind === 'close_facility' || kind === 'set_node_field') && (
          <Field
            id={`${ids}-code`}
            label="Facility code"
            list={`${ids}-nodes`}
            value={draft.code ?? ''}
            onChange={(v) => set('code', v)}
          />
        )}
        {kind === 'set_node_field' && (
          <>
            <label htmlFor={`${ids}-nf`} className="tiny dim">
              Field
            </label>
            <select
              id={`${ids}-nf`}
              className="tag-input"
              value={draft.field ?? ''}
              onChange={(e) => set('field', e.target.value)}
            >
              <option value="">field…</option>
              {NODE_FIELDS.map((f) => (
                <option key={f} value={f}>
                  {f.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
            <Field
              id={`${ids}-nv`}
              label="Value"
              value={draft.value ?? ''}
              onChange={(v) => set('value', v)}
            />
          </>
        )}
        {kind === 'scale_demand' && (
          <>
            <Field
              id={`${ids}-factor`}
              label="Factor (1.2 = up a fifth)"
              value={draft.factor ?? ''}
              onChange={(v) => set('factor', v)}
            />
            <Field
              id={`${ids}-admin1`}
              label="Only in province (optional)"
              value={draft.admin1 ?? ''}
              onChange={(v) => set('admin1', v)}
            />
            <Field
              id={`${ids}-sku`}
              label="Only for product SKU (optional)"
              value={draft.sku ?? ''}
              onChange={(v) => set('sku', v)}
            />
          </>
        )}
        {kind === 'add_node' && (
          <>
            <Field
              id={`${ids}-ac`}
              label="Store code"
              value={draft.code ?? ''}
              onChange={(v) => set('code', v)}
            />
            <Field
              id={`${ids}-an`}
              label="Store name"
              value={draft.name ?? ''}
              onChange={(v) => set('name', v)}
            />
            <Field
              id={`${ids}-alat`}
              label="Latitude"
              value={draft.lat ?? ''}
              onChange={(v) => set('lat', v)}
            />
            <Field
              id={`${ids}-alon`}
              label="Longitude"
              value={draft.lon ?? ''}
              onChange={(v) => set('lon', v)}
            />
            <Field
              id={`${ids}-afc`}
              label="Annual fixed cost"
              value={draft.hub_fixed_cost ?? ''}
              onChange={(v) => set('hub_fixed_cost', v)}
            />
            <Field
              id={`${ids}-acx`}
              label="One-off cost to open"
              value={draft.hub_open_capex ?? ''}
              onChange={(v) => set('hub_open_capex', v)}
            />
            <Field
              id={`${ids}-ath`}
              label="Throughput m³ a year"
              value={draft.hub_throughput_m3 ?? ''}
              onChange={(v) => set('hub_throughput_m3', v)}
            />
          </>
        )}
        {kind === 'add_lane' && (
          <>
            <Field
              id={`${ids}-lf`}
              label="From facility code"
              list={`${ids}-nodes`}
              value={draft.from_code ?? ''}
              onChange={(v) => set('from_code', v)}
            />
            <Field
              id={`${ids}-lt`}
              label="To facility code"
              list={`${ids}-nodes`}
              value={draft.to_code ?? ''}
              onChange={(v) => set('to_code', v)}
            />
            <Field
              id={`${ids}-lm`}
              label="Mode (road, sea, air, river)"
              value={draft.mode ?? ''}
              onChange={(v) => set('mode', v)}
            />
            <Field
              id={`${ids}-lc`}
              label="Cost per m³ (optional)"
              value={draft.cost_per_m3 ?? ''}
              onChange={(v) => set('cost_per_m3', v)}
            />
            <Field
              id={`${ids}-lcap`}
              label="m³ per trip (optional)"
              value={draft.capacity_per_trip_m3 ?? ''}
              onChange={(v) => set('capacity_per_trip_m3', v)}
            />
          </>
        )}
        {(kind === 'remove_lane' || kind === 'set_lane_field') && (
          <Field
            id={`${ids}-lcode`}
            label="Lane code"
            list={`${ids}-lanes`}
            value={draft.code ?? ''}
            onChange={(v) => set('code', v)}
          />
        )}
        {kind === 'set_lane_field' && (
          <>
            <label htmlFor={`${ids}-lfld`} className="tiny dim">
              Field
            </label>
            <select
              id={`${ids}-lfld`}
              className="tag-input"
              value={draft.field ?? ''}
              onChange={(e) => set('field', e.target.value)}
            >
              <option value="">field…</option>
              {LANE_FIELDS.map((f) => (
                <option key={f} value={f}>
                  {f.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
            <Field
              id={`${ids}-lv`}
              label="Value"
              value={draft.value ?? ''}
              onChange={(v) => set('value', v)}
            />
          </>
        )}
        <div className="session-actions">
          <button type="submit" className="btn small primary" disabled={!candidate}>
            Add change
          </button>
        </div>
      </form>
    </div>
  )
}

function Field({
  id,
  label,
  value,
  onChange,
  list,
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  list?: string
}) {
  return (
    <label htmlFor={id} className="tiny dim">
      {label}
      <input
        id={id}
        className="tag-input"
        list={list}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}
