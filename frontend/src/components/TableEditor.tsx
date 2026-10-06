import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { api } from '../api'
import type { ConfidenceMarker, TableName } from '../types'
import { MarkerPicker } from './FacilityEditor'
import { Help } from './Help'
import { HELP } from '../help'

/**
 * The table editor: every model table as a grid.
 *
 * Facilities, lanes, products and demand, each with filter, sort, a cell you can
 * click and type into, add, retire and restore, and the one thing a grid needs that
 * a form does not: tick many rows and set one field on all of them. Every cell change
 * goes through the same editing API as the facility editor, so it carries a name, a
 * marker of how sure, and a reason, and can be reverted from the Provenance tab.
 */

type Row = Record<string, unknown> & { id: number; retired_at?: string | null }

type Column = {
  key: string
  label: string
  kind: 'text' | 'number' | 'bool' | 'select'
  options?: string[]
  editable?: boolean
  bulk?: boolean
  width?: number
  render?: (row: Row) => string
}

const TABLE_LABELS: Record<TableName, string> = {
  nodes: 'Facilities and stores',
  edges: 'Lanes',
  products: 'Products',
  demand: 'Demand',
}

const COLUMNS: Record<TableName, Column[]> = {
  nodes: [
    { key: 'code', label: 'Code', kind: 'text' },
    { key: 'name', label: 'Name', kind: 'text', editable: true },
    { key: 'level', label: 'Level', kind: 'number', editable: true, bulk: true, width: 60 },
    { key: 'type', label: 'Type', kind: 'text', editable: true, bulk: true },
    { key: 'admin1', label: 'Province', kind: 'text', editable: true, bulk: true },
    { key: 'admin2', label: 'District', kind: 'text', editable: true, bulk: true },
    { key: 'lat', label: 'Lat', kind: 'number', editable: true, width: 80 },
    { key: 'lon', label: 'Lon', kind: 'number', editable: true, width: 80 },
    { key: 'catchment_population', label: 'Population', kind: 'number', editable: true, bulk: true },
    {
      key: 'operating_status',
      label: 'Status',
      kind: 'select',
      options: ['operational', 'non_operational', 'planned'],
      editable: true,
      bulk: true,
    },
    { key: 'terrain_class', label: 'Terrain', kind: 'text', editable: true, bulk: true },
    { key: 'hub_capable', label: 'Hub', kind: 'bool', editable: true, bulk: true, width: 50 },
    { key: 'hub_fixed_cost', label: 'Hub cost/yr', kind: 'number', editable: true, bulk: true },
    { key: 'hub_throughput_m3', label: 'Hub m³/yr', kind: 'number', editable: true, bulk: true },
  ],
  edges: [
    { key: 'code', label: 'Code', kind: 'text' },
    { key: 'from_code', label: 'From', kind: 'text' },
    { key: 'to_code', label: 'To', kind: 'text' },
    {
      key: 'mode',
      label: 'Mode',
      kind: 'select',
      options: ['road', 'sea', 'air', 'river', 'foot', 'drone'],
      editable: true,
      bulk: true,
    },
    { key: 'distance_km', label: 'km', kind: 'number', editable: true, width: 70 },
    { key: 'base_travel_time_hr', label: 'Hours', kind: 'number', editable: true, width: 60 },
    { key: 'service_name', label: 'Service', kind: 'text', editable: true, bulk: true },
    {
      key: 'service_frequency',
      label: 'Frequency',
      kind: 'select',
      options: [
        '',
        'DAILY',
        'TWICE_WEEKLY',
        'WEEKLY',
        'FORTNIGHTLY',
        'MONTHLY',
        'SIX_WEEKLY',
        'QUARTERLY',
        'BIANNUAL',
      ],
      editable: true,
      bulk: true,
    },
    { key: 'capacity_per_trip_m3', label: 'm³/trip', kind: 'number', editable: true, bulk: true, width: 70 },
    { key: 'cost_per_m3', label: 'Cost/m³', kind: 'number', editable: true, bulk: true, width: 70 },
    { key: 'fixed_cost_per_trip', label: 'Cost/trip', kind: 'number', editable: true, bulk: true, width: 70 },
    { key: 'variable_cost_per_km', label: 'Cost/km', kind: 'number', editable: true, bulk: true, width: 70 },
    { key: 'reliability', label: 'Reliability', kind: 'number', editable: true, bulk: true, width: 70 },
    { key: 'active', label: 'Active', kind: 'bool', editable: true, bulk: true, width: 50 },
  ],
  products: [
    { key: 'sku', label: 'SKU', kind: 'text' },
    { key: 'name', label: 'Name', kind: 'text', editable: true },
    {
      key: 'temperature_band',
      label: 'Band',
      kind: 'select',
      options: ['ambient', '+2-8', '-20', '-70'],
      editable: true,
      bulk: true,
    },
    { key: 'volume_per_unit_cm3', label: 'cm³/unit', kind: 'number', editable: true, bulk: true },
    { key: 'unit_cost', label: 'Unit cost', kind: 'number', editable: true, bulk: true },
    { key: 'shelf_life_days', label: 'Shelf life (d)', kind: 'number', editable: true, bulk: true },
  ],
  demand: [
    { key: 'node_code', label: 'Facility', kind: 'text', render: (r) => `${r.node_name ?? r.node_code}` },
    { key: 'admin1', label: 'Province', kind: 'text' },
    { key: 'sku', label: 'Product', kind: 'text' },
    { key: 'period', label: 'Period', kind: 'number', width: 60 },
    { key: 'quantity', label: 'Quantity', kind: 'number', editable: true, bulk: true },
    { key: 'source', label: 'Source', kind: 'text' },
    {
      key: 'derivation',
      label: 'Estimated',
      kind: 'text',
      render: (r) => (r.derivation ? 'yes' : ''),
      width: 70,
    },
  ],
}

function display(row: Row, column: Column): string {
  if (column.render) return column.render(row)
  const value = row[column.key]
  if (value === null || value === undefined) return ''
  if (column.kind === 'bool') return value ? 'yes' : 'no'
  if (typeof value === 'number')
    return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, '')
  return String(value)
}

function parse(text: string, column: Column): unknown {
  if (column.kind === 'number') {
    const n = Number(text.replace(/,/g, '').trim())
    return Number.isNaN(n) ? null : n
  }
  if (column.kind === 'bool') return /^(y|yes|true|1)$/i.test(text.trim())
  return text.trim() === '' ? null : text.trim()
}

export function TableEditor({
  countryId,
  onChanged,
  onMessage,
}: {
  countryId: number
  onChanged: () => void
  onMessage: (message: string | null) => void
}) {
  const [table, setTable] = useState<TableName>('nodes')
  const [rows, setRows] = useState<Row[]>([])
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null)
  const [showRetired, setShowRetired] = useState(false)
  const [editing, setEditing] = useState<{ id: number; key: string; draft: string } | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkField, setBulkField] = useState('')
  const [bulkValue, setBulkValue] = useState('')
  const [marker, setMarker] = useState<ConfidenceMarker>('I')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [limit, setLimit] = useState(200)
  const ids = useId()

  const reload = useCallback(async () => {
    try {
      setRows(await api.table<Row>(countryId, table))
    } catch (error) {
      onMessage(String(error))
    }
  }, [countryId, table, onMessage])

  useEffect(() => {
    setSelected(new Set())
    setEditing(null)
    setSort(null)
    setLimit(200)
    void reload()
  }, [reload])

  const columns = COLUMNS[table]
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    let list = rows.filter((row) => (showRetired ? true : !row.retired_at))
    if (q) list = list.filter((row) => columns.some((c) => display(row, c).toLowerCase().includes(q)))
    if (sort) {
      const column = columns.find((c) => c.key === sort.key)
      if (column) {
        list = [...list].sort((a, b) => {
          const va = a[sort.key],
            vb = b[sort.key]
          if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sort.dir
          return display(a, column).localeCompare(display(b, column)) * sort.dir
        })
      }
    }
    return list
  }, [rows, query, sort, columns, showRetired])

  async function saveCell(row: Row, column: Column, text: string) {
    setEditing(null)
    const value = parse(text, column)
    if (value === null && column.kind !== 'text') return
    if (display(row, column) === text.trim()) return
    setBusy(true)
    try {
      if (table === 'nodes')
        await api.patchNode(row.id, { [column.key]: value, confidence_marker: marker, reason } as never)
      else if (table === 'edges')
        await api.overrideEdge(row.id, { [column.key]: value, rationale: reason } as never)
      else if (table === 'products')
        await api.patchProduct(row.id, { [column.key]: value, confidence_marker: marker, reason })
      else await api.setDemandRow(row.id, { sku: String(row.sku), quantity: Number(value) })
      onMessage(`Saved ${column.label.toLowerCase()} for ${String(row.code ?? row.sku ?? row.node_code)}.`)
      await reload()
      onChanged()
    } catch (error) {
      onMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  async function bulk() {
    const column = columns.find((c) => c.key === bulkField)
    if (!column || !selected.size) return
    const value = parse(bulkValue, column)
    if (value === null && column.kind !== 'text') return
    if (!window.confirm(`Set ${column.label.toLowerCase()} to “${bulkValue}” on ${selected.size} rows?`))
      return
    setBusy(true)
    try {
      const outcome = await api.bulkSet(countryId, table, {
        ids: [...selected],
        field: column.key,
        value,
        confidence_marker: marker,
        reason,
      })
      onMessage(
        `Set ${column.label.toLowerCase()} on ${outcome.changed} of ${outcome.of} rows` +
          (outcome.recomputed ? `; ${outcome.recomputed} estimates followed` : '') +
          '. One ledger row each; revert them together from the Provenance tab.',
      )
      setSelected(new Set())
      await reload()
      onChanged()
    } catch (error) {
      onMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  async function retireOrRestore(row: Row) {
    const retired = Boolean(row.retired_at)
    const label = String(row.code ?? row.sku ?? row.id)
    const why = retired
      ? ''
      : (window.prompt(`Retire ${label}? Give a reason (it is kept, not deleted).`) ?? null)
    if (!retired && why === null) return
    setBusy(true)
    try {
      if (table === 'nodes')
        retired
          ? await api.restoreNode(row.id, 'Restored from the table.')
          : await api.retireNode(row.id, why || 'Retired from the table.')
      else if (table === 'edges')
        retired
          ? await api.restoreEdge(row.id, 'Restored from the table.')
          : await api.retireEdge(row.id, why || 'Retired from the table.')
      else if (table === 'products')
        retired
          ? await api.restoreProduct(row.id, 'Restored from the table.')
          : await api.retireProduct(row.id, why || 'Retired from the table.')
      onMessage(`${retired ? 'Restored' : 'Retired'} ${label}.`)
      await reload()
      onChanged()
    } catch (error) {
      onMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  async function add() {
    setBusy(true)
    try {
      if (table === 'edges')
        await api.createEdge(countryId, {
          ...draft,
          capacity_per_trip_m3: Number(draft.capacity_per_trip_m3 || 0),
          cost_per_m3: Number(draft.cost_per_m3 || 0),
          confidence_marker: marker,
          reason,
        })
      else if (table === 'products')
        await api.createProduct(countryId, {
          ...draft,
          volume_per_unit_cm3: Number(draft.volume_per_unit_cm3 || 0),
          unit_cost: Number(draft.unit_cost || 0),
          shelf_life_days: Number(draft.shelf_life_days || 730),
          confidence_marker: marker,
          reason,
        })
      setAdding(false)
      setDraft({})
      onMessage(`Added ${draft.code ?? draft.sku}.`)
      await reload()
      onChanged()
    } catch (error) {
      onMessage(String(error))
    } finally {
      setBusy(false)
    }
  }

  const bulkColumns = columns.filter((c) => c.bulk)
  const allVisibleSelected = visible.length > 0 && visible.slice(0, limit).every((r) => selected.has(r.id))

  return (
    <div className="section table-editor" data-tour="tables">
      <h3>
        Edit the tables
        <Help text={HELP.tables} label="the table editor" />
      </h3>
      <div className="session-actions" style={{ marginBottom: 8 }}>
        <label htmlFor={`${ids}-table`} className="visually-hidden">
          Table
        </label>
        <select
          id={`${ids}-table`}
          className="tag-input"
          style={{ maxWidth: 200 }}
          value={table}
          onChange={(e) => setTable(e.target.value as TableName)}
          title="Which table the grid shows"
        >
          {(Object.keys(TABLE_LABELS) as TableName[]).map((name) => (
            <option key={name} value={name}>
              {TABLE_LABELS[name]}
            </option>
          ))}
        </select>
        <a
          className="btn small ghost"
          href={api.tableCsvUrl(countryId, table)}
          download
          title="This table as a CSV file, in the template's columns, importable back on its own"
        >
          Download .csv
        </a>
        <label htmlFor={`${ids}-q`} className="visually-hidden">
          Filter rows
        </label>
        <input
          id={`${ids}-q`}
          type="search"
          className="tag-input"
          style={{ maxWidth: 200 }}
          placeholder="Filter…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="checkbox tiny">
          <input type="checkbox" checked={showRetired} onChange={(e) => setShowRetired(e.target.checked)} />{' '}
          show retired
        </label>
        {(table === 'edges' || table === 'products') && (
          <button type="button" className="btn small" onClick={() => setAdding((v) => !v)}>
            {adding ? 'Cancel' : table === 'edges' ? 'Add lane…' : 'Add product…'}
          </button>
        )}
        <span className="tiny dim">
          {visible.length} of {rows.length} rows
        </span>
      </div>

      <div className="session-actions" style={{ marginBottom: 8 }}>
        <MarkerPicker value={marker} onChange={setMarker} />
        <label htmlFor={`${ids}-reason`} className="visually-hidden">
          Reason for the change
        </label>
        <input
          id={`${ids}-reason`}
          className="tag-input"
          style={{ maxWidth: 260 }}
          placeholder="Reason (kept beside every change)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>

      {adding && (
        <form
          className="session-form"
          style={{ marginBottom: 8 }}
          onSubmit={(e) => {
            e.preventDefault()
            void add()
          }}
        >
          {(table === 'edges'
            ? [
                'code',
                'from_code',
                'to_code',
                'mode',
                'service_frequency',
                'capacity_per_trip_m3',
                'cost_per_m3',
              ]
            : ['sku', 'name', 'temperature_band', 'volume_per_unit_cm3', 'unit_cost', 'shelf_life_days']
          ).map((key) => (
            <label key={key} className="tiny dim">
              {key.replace(/_/g, ' ')}
              <input
                className="tag-input"
                value={draft[key] ?? ''}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </label>
          ))}
          <div className="session-actions">
            <button type="submit" className="btn small primary" disabled={busy}>
              Add
            </button>
          </div>
        </form>
      )}

      {selected.size > 0 && (
        <div className="callout bulk-bar" role="region" aria-label="Set a field on the selected rows">
          <b>{selected.size} selected.</b> Set{' '}
          <label htmlFor={`${ids}-bf`} className="visually-hidden">
            Field
          </label>
          <select
            id={`${ids}-bf`}
            className="tag-input"
            style={{ maxWidth: 160, display: 'inline-block' }}
            value={bulkField}
            onChange={(e) => setBulkField(e.target.value)}
          >
            <option value="">field…</option>
            {bulkColumns.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>{' '}
          to{' '}
          <label htmlFor={`${ids}-bv`} className="visually-hidden">
            Value
          </label>
          {columns.find((c) => c.key === bulkField)?.kind === 'select' ? (
            <select
              id={`${ids}-bv`}
              className="tag-input"
              style={{ maxWidth: 160, display: 'inline-block' }}
              value={bulkValue}
              onChange={(e) => setBulkValue(e.target.value)}
            >
              <option value="">value…</option>
              {(columns.find((c) => c.key === bulkField)?.options ?? []).map((o) => (
                <option key={o} value={o}>
                  {o || '(blank)'}
                </option>
              ))}
            </select>
          ) : (
            <input
              id={`${ids}-bv`}
              className="tag-input"
              style={{ maxWidth: 140, display: 'inline-block' }}
              value={bulkValue}
              onChange={(e) => setBulkValue(e.target.value)}
            />
          )}{' '}
          <button
            type="button"
            className="btn small primary"
            onClick={bulk}
            disabled={busy || !bulkField || !bulkValue.trim()}
          >
            Apply to {selected.size}
          </button>{' '}
          <button type="button" className="btn small ghost" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      <div
        className="scroll-x grid-wrap"
        tabIndex={0}
        role="region"
        aria-label={`${TABLE_LABELS[table]} table`}
      >
        <table className="grid">
          <thead>
            <tr>
              <th style={{ width: 28 }}>
                <input
                  type="checkbox"
                  aria-label="Select all visible rows"
                  checked={allVisibleSelected}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked ? new Set(visible.slice(0, limit).map((r) => r.id)) : new Set(),
                    )
                  }
                />
              </th>
              {columns.map((c) => (
                <th
                  key={c.key}
                  style={c.width ? { width: c.width } : undefined}
                  aria-sort={sort?.key === c.key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}
                >
                  <button
                    type="button"
                    className="th-sort"
                    onClick={() =>
                      setSort((s) =>
                        s?.key === c.key ? { key: c.key, dir: s.dir === 1 ? -1 : 1 } : { key: c.key, dir: 1 },
                      )
                    }
                  >
                    {c.label}
                    {sort?.key === c.key ? (sort.dir === 1 ? ' ↑' : ' ↓') : ''}
                  </button>
                </th>
              ))}
              {table !== 'demand' && <th />}
            </tr>
          </thead>
          <tbody>
            {visible.slice(0, limit).map((row) => (
              <tr key={row.id} className={row.retired_at ? 'retired' : ''}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Select ${String(row.code ?? row.sku ?? row.node_code)}`}
                    checked={selected.has(row.id)}
                    onChange={(e) => {
                      const next = new Set(selected)
                      e.target.checked ? next.add(row.id) : next.delete(row.id)
                      setSelected(next)
                    }}
                  />
                </td>
                {columns.map((c) => {
                  const isEditing = editing?.id === row.id && editing.key === c.key
                  const canEdit = Boolean(c.editable) && !row.retired_at
                  return (
                    <td
                      key={c.key}
                      className={`${c.kind === 'number' ? 'n' : ''}${canEdit ? ' editable' : ''}`}
                    >
                      {isEditing ? (
                        c.kind === 'select' ? (
                          <select
                            className="cell-input"
                            autoFocus
                            value={editing.draft}
                            aria-label={`${c.label} for ${String(row.code ?? row.sku ?? row.node_code)}`}
                            onChange={(e) => void saveCell(row, c, e.target.value)}
                            onBlur={() => setEditing(null)}
                          >
                            {(c.options ?? []).map((o) => (
                              <option key={o} value={o}>
                                {o || '(blank)'}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            className="cell-input"
                            autoFocus
                            value={editing.draft}
                            aria-label={`${c.label} for ${String(row.code ?? row.sku ?? row.node_code)}`}
                            onChange={(e) => setEditing({ ...editing, draft: e.target.value })}
                            onBlur={() => void saveCell(row, c, editing.draft)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') void saveCell(row, c, editing.draft)
                              if (e.key === 'Escape') setEditing(null)
                            }}
                          />
                        )
                      ) : canEdit ? (
                        <button
                          type="button"
                          className="cell-button"
                          title="Click to edit"
                          onClick={() =>
                            setEditing({
                              id: row.id,
                              key: c.key,
                              draft: c.kind === 'bool' ? (row[c.key] ? 'no' : 'yes') : display(row, c),
                            })
                          }
                        >
                          {display(row, c) || <span className="dim">—</span>}
                        </button>
                      ) : (
                        display(row, c) || <span className="dim">—</span>
                      )}
                    </td>
                  )
                })}
                {table !== 'demand' && (
                  <td>
                    <button
                      type="button"
                      className="btn small ghost"
                      onClick={() => retireOrRestore(row)}
                      disabled={busy}
                    >
                      {row.retired_at ? 'Restore' : 'Retire'}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {visible.length > limit && (
        <button
          type="button"
          className="btn small ghost"
          style={{ marginTop: 6 }}
          onClick={() => setLimit((l) => l + 200)}
        >
          Show {Math.min(200, visible.length - limit)} more
        </button>
      )}
      <div className="lever-note" style={{ marginTop: 6 }}>
        Click a cell to edit it; Enter saves, Escape cancels. A yes/no cell flips on click. Every change is a
        ledger row with the marker and reason above.
      </div>
    </div>
  )
}
