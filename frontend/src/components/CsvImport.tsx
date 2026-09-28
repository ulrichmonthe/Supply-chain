import { useId, useState } from 'react'
import { api } from '../api'
import type { CsvInspection, ValidationReport } from '../types'

/**
 * The column mapper: any CSV, mapped once to our columns, into the same hallway.
 *
 * A ministry's facility register, a warehouse system's consumption extract, a
 * procurement catalogue: each arrives as a CSV with its own headers. Map them to ours
 * here, keep the mapping under a name, and the rows go through the same validate,
 * preview and apply steps as a workbook. One sheet at a time, merged, never a wipe.
 */
export function CsvImport({
  countryId,
  busy,
  onBusy,
  onValidated,
  onMessage,
}: {
  countryId: number
  busy: boolean
  onBusy: (value: boolean) => void
  onValidated: (report: ValidationReport) => void
  onMessage: (message: string | null) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [inspection, setInspection] = useState<CsvInspection | null>(null)
  const [sheet, setSheet] = useState<string>('Nodes')
  const [mapping, setMapping] = useState<Record<string, string | null>>({})
  const [saveAs, setSaveAs] = useState('')
  const ids = useId()

  async function inspect(chosen: File) {
    onBusy(true)
    onMessage(null)
    try {
      const result = await api.inspectCsv(countryId, chosen)
      setFile(chosen)
      setInspection(result)
      setSheet(result.sheet)
      setMapping(result.guesses[result.sheet] ?? {})
      setSaveAs(result.matched_saved ?? '')
    } catch (error) {
      onMessage(String(error))
    } finally {
      onBusy(false)
    }
  }

  function chooseSheet(next: string) {
    setSheet(next)
    setMapping(inspection?.guesses[next] ?? {})
  }

  function applySaved(name: string) {
    const saved = inspection?.saved.find((s) => s.name === name)
    if (!saved) return
    setSheet(saved.sheet)
    setMapping({ ...(inspection?.guesses[saved.sheet] ?? {}), ...saved.mapping })
    setSaveAs(saved.name)
  }

  async function validate() {
    if (!file || !inspection) return
    onBusy(true)
    onMessage(null)
    try {
      const report = await api.importCsv(countryId, file, sheet, mapping, saveAs.trim())
      onValidated(report)
      setInspection(null)
      setFile(null)
    } catch (error) {
      onMessage(String(error))
    } finally {
      onBusy(false)
    }
  }

  const fields = inspection?.fields[sheet] ?? []
  const missing = fields.filter((f) => f.required && !mapping[f.key]).map((f) => f.key)
  const used = new Set(Object.values(mapping).filter(Boolean))
  const sample = inspection?.sample[0] ?? {}

  return (
    <div className="section csv-import">
      <h3>Import a CSV from another system</h3>
      <p className="lever-note" style={{ marginBottom: 8 }}>
        A facility register, a consumption extract, a product catalogue. Map its columns to ours once; the
        mapping is kept for next time. One sheet per file, merged into what is loaded, never a wipe.
      </p>
      {!inspection && (
        <label className="btn small" style={{ display: 'inline-block' }}>
          Choose a CSV…
          <input
            type="file"
            accept=".csv,.tsv,.txt,text/csv"
            style={{ display: 'none' }}
            disabled={busy}
            onChange={(event) => {
              const chosen = event.target.files?.[0]
              if (chosen) void inspect(chosen)
              event.target.value = ''
            }}
          />
        </label>
      )}

      {inspection && (
        <div className="csv-mapper">
          <div className="row-note" style={{ marginBottom: 6 }}>
            <b>{inspection.filename}</b> · {inspection.row_count} rows · {inspection.columns.length} columns
            {inspection.matched_saved ? ` · matches the saved mapping “${inspection.matched_saved}”` : ''}
          </div>
          <div className="session-actions" style={{ marginBottom: 8 }}>
            <label htmlFor={`${ids}-sheet`} className="tiny dim">
              This file is a list of
            </label>
            <select
              id={`${ids}-sheet`}
              className="tag-input"
              style={{ maxWidth: 200 }}
              value={sheet}
              onChange={(e) => chooseSheet(e.target.value)}
            >
              <option value="Nodes">Facilities and stores</option>
              <option value="Edges">Lanes</option>
              <option value="Products">Products</option>
              <option value="Demand">Demand or consumption</option>
            </select>
            {inspection.saved.length > 0 && (
              <>
                <label htmlFor={`${ids}-saved`} className="visually-hidden">
                  Apply a saved mapping
                </label>
                <select
                  id={`${ids}-saved`}
                  className="tag-input"
                  style={{ maxWidth: 220 }}
                  value=""
                  onChange={(e) => applySaved(e.target.value)}
                >
                  <option value="">Saved mappings…</option>
                  {inspection.saved.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.name} ({s.sheet})
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>

          <div className="scroll-x" tabIndex={0} role="region" aria-label="Column mapping">
            <table className="mapping-table">
              <thead>
                <tr>
                  <th>Our column</th>
                  <th>Column in the file</th>
                  <th>First row</th>
                </tr>
              </thead>
              <tbody>
                {fields.map((field) => (
                  <tr key={field.key} className={field.required && !mapping[field.key] ? 'missing' : ''}>
                    <td>
                      <label htmlFor={`${ids}-${field.key}`}>
                        {field.key}
                        {field.required && <span className="dim"> · required</span>}
                      </label>
                      <div className="tiny dim">{field.description}</div>
                    </td>
                    <td>
                      <select
                        id={`${ids}-${field.key}`}
                        className="tag-input"
                        value={mapping[field.key] ?? ''}
                        onChange={(e) => setMapping((m) => ({ ...m, [field.key]: e.target.value || null }))}
                      >
                        <option value="">— not in this file —</option>
                        {inspection.columns
                          .filter((c) => c)
                          .map((column) => (
                            <option key={column} value={column}>
                              {column}
                              {used.has(column) && mapping[field.key] !== column ? ' (used)' : ''}
                            </option>
                          ))}
                      </select>
                    </td>
                    <td className="dim">
                      {mapping[field.key] ? (sample[mapping[field.key] as string] ?? '') : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="session-form" style={{ marginTop: 8 }}>
            <label htmlFor={`${ids}-save`} className="tiny dim">
              Keep this mapping as
            </label>
            <input
              id={`${ids}-save`}
              className="tag-input"
              value={saveAs}
              placeholder="e.g. Ministry facility list (optional)"
              maxLength={80}
              onChange={(e) => setSaveAs(e.target.value)}
            />
          </div>
          {missing.length > 0 && (
            <div className="lever-note" role="status">
              Still needed: {missing.join(', ')}.
            </div>
          )}
          <div className="session-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="btn small primary"
              onClick={validate}
              disabled={busy || missing.length > 0}
            >
              Validate the file
            </button>
            <button
              type="button"
              className="btn small ghost"
              onClick={() => {
                setInspection(null)
                setFile(null)
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
