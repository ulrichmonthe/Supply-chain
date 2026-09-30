import { useCallback, useEffect, useId, useState } from 'react'
import { api, getAuthor } from '../api'
import type {
  OnboardingFile,
  OnboardingItem,
  OnboardingMatch,
  OnboardingPack,
  OnboardingRecord,
  OnboardingReport,
  OnboardingRun,
} from '../types'

/**
 * Onboarding: messy ministry files in, a signed-off, provenance-tagged dataset out.
 *
 * Six steps, read left to right: the country pack that must be approved first; the
 * files with their checksums and vintages; the facilities reconciled into a crosswalk
 * the ministry owns; the checks and the estimates; the review queue, sorted by what
 * moves the result most; and the report a named approver signs before anyone loads a
 * thing. Nothing here writes to the model. Loading is the last button, on its own.
 */

const STEPS = ['Pack', 'Files', 'Facilities', 'Checks', 'Review', 'Sign-off'] as const
type Step = (typeof STEPS)[number]

const CLASS_ORDER = ['observed', 'converted', 'confirmed', 'estimated', 'illustrative', 'missing'] as const

export function OnboardingPanel({ countryId, onLoaded }: { countryId: number; onLoaded: () => void }) {
  const [pack, setPack] = useState<OnboardingPack | null>(null)
  const [runs, setRuns] = useState<OnboardingRun[]>([])
  const [run, setRun] = useState<OnboardingRun | null>(null)
  const [step, setStep] = useState<Step>('Pack')
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reloadRuns = useCallback(async () => {
    const list = await api.onboardingRuns(countryId)
    setRuns(list)
    setRun((current) => (current ? (list.find((r) => r.id === current.id) ?? current) : (list[0] ?? null)))
  }, [countryId])

  useEffect(() => {
    api
      .onboardingPack(countryId)
      .then(setPack)
      .catch((e) => setMessage(String(e)))
    void reloadRuns().catch((e) => setMessage(String(e)))
  }, [countryId, reloadRuns])

  async function act<T>(fn: () => Promise<T>, after?: (value: T) => void): Promise<T | undefined> {
    setBusy(true)
    setMessage(null)
    try {
      const value = await fn()
      after?.(value)
      return value
    } catch (e) {
      setMessage(String(e))
      return undefined
    } finally {
      setBusy(false)
    }
  }

  const refreshRun = useCallback(async () => {
    if (!run) return
    const fresh = await api.onboardingRun(run.id)
    setRun(fresh)
    setRuns((list) => list.map((r) => (r.id === fresh.id ? fresh : r)))
  }, [run])

  return (
    <div className="onboarding">
      <div className="section">
        <h3>Onboard a ministry's data</h3>
        <p className="lever-note" style={{ marginBottom: 8 }}>
          Files in, a validated dataset out, every value carrying where it came from and how sure someone was.
          A named approver signs before anything reaches the model; loading is a separate step.
        </p>
        {message && (
          <div className="callout bad" role="alert" style={{ marginBottom: 8 }}>
            {message}
          </div>
        )}
        <RunPicker
          runs={runs}
          run={run}
          onPick={(r) => {
            setRun(r)
            setStep(r ? 'Files' : 'Pack')
          }}
          onCreate={(name, kind) =>
            act(
              () => api.createOnboardingRun(countryId, { name, kind }),
              (created) => {
                setRuns((list) => [created, ...list])
                setRun(created)
                setStep('Files')
              },
            )
          }
          busy={busy}
          packApproved={pack?.status === 'approved'}
        />
      </div>

      <div className="tabs onboarding-steps" role="tablist" aria-label="Onboarding steps">
        {STEPS.map((name, index) => (
          <button
            type="button"
            key={name}
            role="tab"
            aria-selected={step === name}
            tabIndex={step === name ? 0 : -1}
            className={`tab${step === name ? ' on' : ''}`}
            onClick={() => setStep(name)}
            disabled={name !== 'Pack' && !run}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                e.preventDefault()
                const next = STEPS[(index + (e.key === 'ArrowRight' ? 1 : STEPS.length - 1)) % STEPS.length]
                if (next === 'Pack' || run) setStep(next)
              }
            }}
          >
            <span className="step-n">{index + 1}</span> {name}
          </button>
        ))}
      </div>

      <div role="tabpanel" className="onboarding-body">
        {step === 'Pack' && pack && (
          <PackStep pack={pack} countryId={countryId} busy={busy} onChanged={(p) => setPack(p)} act={act} />
        )}
        {run && step === 'Files' && <FilesStep run={run} busy={busy} act={act} refresh={refreshRun} />}
        {run && step === 'Facilities' && (
          <FacilitiesStep run={run} countryId={countryId} busy={busy} act={act} refresh={refreshRun} />
        )}
        {run && step === 'Checks' && <ChecksStep run={run} busy={busy} act={act} refresh={refreshRun} />}
        {run && step === 'Review' && <ReviewStep run={run} busy={busy} act={act} refresh={refreshRun} />}
        {run && step === 'Sign-off' && (
          <SignOffStep
            run={run}
            busy={busy}
            act={act}
            refresh={async () => {
              await refreshRun()
            }}
            onLoaded={onLoaded}
          />
        )}
      </div>
    </div>
  )
}

// --- the run picker ---------------------------------------------------------------------

function RunPicker({
  runs,
  run,
  onPick,
  onCreate,
  busy,
  packApproved,
}: {
  runs: OnboardingRun[]
  run: OnboardingRun | null
  onPick: (run: OnboardingRun | null) => void
  onCreate: (name: string, kind: 'initial' | 'refresh') => void
  busy: boolean
  packApproved: boolean
}) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'initial' | 'refresh'>('initial')
  const ids = useId()
  return (
    <div className="run-picker">
      <div className="session-actions">
        <label htmlFor={`${ids}-run`} className="tiny dim">
          Run
        </label>
        <select
          id={`${ids}-run`}
          className="tag-input"
          style={{ maxWidth: 320 }}
          value={run?.id ?? ''}
          onChange={(e) => onPick(runs.find((r) => r.id === Number(e.target.value)) ?? null)}
        >
          <option value="">Choose a run…</option>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name} · {r.kind} · {r.status.replace('_', ' ')} · {r.preparer}
            </option>
          ))}
        </select>
        {run && <StatusPill status={run.status} />}
      </div>
      <div className="session-actions" style={{ marginTop: 6 }}>
        <label htmlFor={`${ids}-name`} className="visually-hidden">
          New run name
        </label>
        <input
          id={`${ids}-name`}
          className="tag-input"
          style={{ maxWidth: 260 }}
          placeholder="New run, e.g. Milne Bay pilot"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label htmlFor={`${ids}-kind`} className="visually-hidden">
          Kind of run
        </label>
        <select
          id={`${ids}-kind`}
          className="tag-input"
          style={{ maxWidth: 150 }}
          value={kind}
          onChange={(e) => setKind(e.target.value as 'initial' | 'refresh')}
        >
          <option value="initial">First onboarding</option>
          <option value="refresh">Refresh</option>
        </select>
        <button
          type="button"
          className="btn small primary"
          disabled={busy || !packApproved || !getAuthor()}
          title={
            !getAuthor()
              ? 'Sign your name first (top bar)'
              : !packApproved
                ? 'The country pack must be approved first'
                : ''
          }
          onClick={() => {
            onCreate(name.trim(), kind)
            setName('')
          }}
        >
          Start run
        </button>
        {!getAuthor() && (
          <span className="tiny dim">Sign your name in the top bar: every step records who did it.</span>
        )}
      </div>
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  const tone =
    status === 'loaded' ? 'good' : status === 'signed_off' ? 'info' : status === 'rejected' ? 'bad' : 'warn'
  return <span className={`pill ${tone}`}>{status.replace('_', ' ')}</span>
}

type Act = <T>(fn: () => Promise<T>, after?: (value: T) => void) => Promise<T | undefined>

// --- 1. the pack ----------------------------------------------------------------------------

function PackStep({
  pack,
  countryId,
  busy,
  onChanged,
  act,
}: {
  pack: OnboardingPack
  countryId: number
  busy: boolean
  onChanged: (p: OnboardingPack) => void
  act: Act
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(() => JSON.stringify(pack.pack, null, 2))
  const [note, setNote] = useState('')
  const p = pack.pack
  return (
    <div className="section">
      <div className="row-note" style={{ marginBottom: 6 }}>
        <b>Country pack v{pack.version}</b> <StatusPill status={pack.status} />
        {pack.reference_example && (
          <span className="pill warn" style={{ marginLeft: 6 }}>
            reference example: every name and threshold is a placeholder
          </span>
        )}
        {pack.approved_by && (
          <span className="tiny dim" style={{ marginLeft: 6 }}>
            approved by {pack.approved_by}
          </span>
        )}
      </div>
      <p className="lever-note">
        Everything that differs between countries and cannot live in code, completed before any data is
        ingested and changed only with approval. Ingestion starts only from an approved pack.
      </p>
      <dl className="pack-summary">
        <dt>Source systems</dt>
        <dd>
          {p.source_systems
            .map((s) => `${s.name} (${s.system}, ${s.cadence || 'cadence unknown'})`)
            .join('; ') || 'none named'}
        </dd>
        <dt>Facility authority</dt>
        <dd>
          {p.facility_authority.authoritative_list
            ? `Authoritative list: ${p.facility_authority.authoritative_list}. `
            : 'No list is authoritative. '}
          Arbiter: {p.facility_authority.arbiter || 'not named'}. Geolocation{' '}
          {p.facility_authority.geolocation_allowed ? 'allowed' : 'not allowed'}.
        </dd>
        <dt>Admin levels</dt>
        <dd>{p.admin_structure.levels.join(' › ')}</dd>
        <dt>Transport</dt>
        <dd>
          {Object.entries(p.transport.modes)
            .map(
              ([mode, r]) =>
                `${mode}: ${r.speed_kmh[0]}–${r.speed_kmh[1]} km/h, ${r.cost_per_m3[0]}–${r.cost_per_m3[1]} per m³${r.seasonal ? ', seasonal' : ''}`,
            )
            .join('; ')}
        </dd>
        <dt>Units and currency</dt>
        <dd>
          {p.products_units.conversions.length} conversions;{' '}
          {Object.keys(p.products_units.per_1000_rates).length} per-1,000 rates; model currency{' '}
          {p.currency.code}
        </dd>
        <dt>Legal profile</dt>
        <dd>
          Data residency {p.legal_profile.data_residency || 'unknown'}; external AI{' '}
          {p.legal_profile.external_ai_allowed ? 'allowed' : 'not allowed'}; data-sharing agreement{' '}
          {p.legal_profile.data_sharing_agreement || 'none'}.
        </dd>
        <dt>Roles</dt>
        <dd>
          Data officers: {p.roles.data_officers.join(', ') || 'none'}. Approvers:{' '}
          {p.roles.approvers.join(', ') || 'none'}.
        </dd>
        <dt>Thresholds</dt>
        <dd>
          Auto-accept a facility match at {p.thresholds.auto_accept_match}; flag a change over{' '}
          {Math.round(p.thresholds.change_sensitivity * 100)}% against the approved dataset;{' '}
          {Object.keys(p.thresholds.ranges).length} absolute ranges.
        </dd>
        <dt>Agent</dt>
        <dd>
          {pack.agent.allowed
            ? `An external model may propose (${pack.agent.provider}).`
            : `Rules only: ${pack.agent.reasons.join('; ')}.`}
        </dd>
      </dl>
      {!pack.completeness.complete && (
        <div className="callout warn">
          <h4>Not complete</h4>
          Still needs: {pack.completeness.missing.map((m) => m.needs).join('; ')}.
        </div>
      )}
      <div className="session-actions" style={{ marginTop: 8 }}>
        <button type="button" className="btn small" onClick={() => setEditing((v) => !v)}>
          {editing ? 'Close editor' : 'Edit as a new version'}
        </button>
        {pack.status === 'draft' && (
          <button
            type="button"
            className="btn small primary"
            disabled={busy || !getAuthor()}
            onClick={() => act(() => api.approveOnboardingPack(pack.id, ''), onChanged)}
          >
            Approve v{pack.version}
          </button>
        )}
      </div>
      {editing && (
        <div style={{ marginTop: 8 }}>
          <label className="field">
            <span>Pack JSON</span>
            <textarea
              className="editor-reason pack-editor"
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={18}
              spellCheck={false}
            />
          </label>
          <label className="field" style={{ marginTop: 6 }}>
            <span>What changed and why</span>
            <input className="tag-input" value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <button
            type="button"
            className="btn small primary"
            style={{ marginTop: 6 }}
            disabled={busy || !getAuthor()}
            onClick={() => {
              let parsed: unknown
              try {
                parsed = JSON.parse(text)
              } catch (e) {
                act(() => Promise.reject(new Error(`That is not valid JSON: ${String(e)}`)))
                return
              }
              act(
                () => api.createOnboardingPack(countryId, { pack: parsed as Record<string, unknown>, note }),
                (created) => {
                  onChanged(created)
                  setEditing(false)
                },
              )
            }}
          >
            Save as v{pack.version + 1} (draft)
          </button>
        </div>
      )}
    </div>
  )
}

// --- 2. files -------------------------------------------------------------------------------

function FilesStep({
  run,
  busy,
  act,
  refresh,
}: {
  run: OnboardingRun
  busy: boolean
  act: Act
  refresh: () => Promise<void>
}) {
  const [vintageFrom, setVintageFrom] = useState('')
  const [vintageTo, setVintageTo] = useState('')
  const [system, setSystem] = useState('')
  const [domain, setDomain] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const ids = useId()
  const locked = run.status !== 'open' && run.status !== 'in_review'

  return (
    <div className="section">
      <p className="lever-note">
        Several files at once. Each is recorded with its checksum, who uploaded it and the period it
        describes, which is not the upload date. The profile says what it looks like; you confirm the mapping;
        staging turns it into records.
      </p>
      {!locked && (
        <div className="session-actions" style={{ flexWrap: 'wrap' }}>
          <label htmlFor={`${ids}-system`} className="visually-hidden">
            Source system
          </label>
          <select
            id={`${ids}-system`}
            className="tag-input"
            style={{ maxWidth: 150 }}
            value={system}
            onChange={(e) => setSystem(e.target.value)}
          >
            <option value="">System: detect</option>
            <option value="dhis2">DHIS2</option>
            <option value="msupply">mSupply</option>
            <option value="openlmis">OpenLMIS</option>
            <option value="excel">Excel / other</option>
          </select>
          <label htmlFor={`${ids}-domain`} className="visually-hidden">
            What the file lists
          </label>
          <select
            id={`${ids}-domain`}
            className="tag-input"
            style={{ maxWidth: 170 }}
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
          >
            <option value="">Lists: detect</option>
            <option value="Nodes">Facilities</option>
            <option value="Demand">Demand or consumption</option>
            <option value="Edges">Lanes or timetables</option>
            <option value="Products">Products</option>
          </select>
          <label htmlFor={`${ids}-from`} className="tiny dim">
            Data vintage
          </label>
          <input
            id={`${ids}-from`}
            className="tag-input"
            style={{ maxWidth: 96 }}
            placeholder="2025-01"
            value={vintageFrom}
            onChange={(e) => setVintageFrom(e.target.value)}
          />
          <input
            aria-label="Vintage to"
            className="tag-input"
            style={{ maxWidth: 96 }}
            placeholder="2025-12"
            value={vintageTo}
            onChange={(e) => setVintageTo(e.target.value)}
          />
          <label className="btn small primary" style={{ display: 'inline-block' }}>
            Add files…
            <input
              type="file"
              multiple
              accept=".csv,.tsv,.txt,.xlsx,.xlsm,text/csv"
              style={{ display: 'none' }}
              disabled={busy}
              onChange={(event) => {
                const chosen = Array.from(event.target.files ?? [])
                if (chosen.length) {
                  void act(
                    () =>
                      api.addOnboardingFiles(run.id, chosen, {
                        source_system: system,
                        domain,
                        vintage_from: vintageFrom,
                        vintage_to: vintageTo,
                      }),
                    () => void refresh(),
                  )
                }
                event.target.value = ''
              }}
            />
          </label>
        </div>
      )}
      {run.files.length === 0 && (
        <p className="row-note" style={{ marginTop: 8 }}>
          No files yet.
        </p>
      )}
      <ul className="file-list">
        {run.files.map((file) => (
          <li key={file.id} className="file-card">
            <div className="file-head">
              <b>{file.filename}</b>{' '}
              <span
                className={`pill ${file.status === 'staged' ? 'good' : file.status === 'mapped' ? 'info' : 'warn'}`}
              >
                {file.status}
              </span>
              <span className="tiny dim">
                {file.source_system} · {file.domain || 'domain?'} · {file.vintage_from || '?'}
                {file.vintage_to && file.vintage_to !== file.vintage_from
                  ? ` to ${file.vintage_to}`
                  : ''} · {Math.round(file.size_bytes / 1024)} KB · {file.uploader} · sha256{' '}
                {file.sha256.slice(0, 12)}…
              </span>
              <button
                type="button"
                className="btn small ghost"
                onClick={() => setOpen(open === file.id ? null : file.id)}
                aria-expanded={open === file.id}
              >
                {open === file.id ? 'Hide' : 'Map and stage'}
              </button>
            </div>
            {open === file.id && (
              <FileMapper file={file} run={run} busy={busy} act={act} refresh={refresh} locked={locked} />
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function FileMapper({
  file,
  busy,
  act,
  refresh,
  locked,
}: {
  file: OnboardingFile
  run: OnboardingRun
  busy: boolean
  act: Act
  refresh: () => Promise<void>
  locked: boolean
}) {
  const sheet =
    file.profile.sheets.find((s) => s.name === (file.mapping.sheet ?? file.profile.primary_sheet)) ??
    file.profile.sheets[0]
  const domain = (file.domain || sheet?.domain || 'Nodes') as 'Nodes' | 'Edges' | 'Products' | 'Demand'
  const [mapping, setMapping] = useState<Record<string, string | null>>(() => ({
    ...(sheet?.guesses[domain] ?? {}),
    ...(file.mapping.mapping ?? {}),
  }))
  const [aux, setAux] = useState<string[]>(file.mapping.aux_columns ?? [])
  const [units, setUnits] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const [column, unit] of Object.entries({ ...(sheet?.units ?? {}), ...(file.mapping.units ?? {}) }))
      if (unit) out[column] = unit
    return out
  })
  const [saveAs, setSaveAs] = useState(sheet?.matched_saved ?? '')
  const [counts, setCounts] = useState<Record<string, number> | null>(null)
  if (!sheet) return <p className="row-note">This file has no table the profiler could read.</p>
  const fields = Object.keys(sheet.guesses[domain] ?? {})
  const sample = sheet.sample[0] ?? {}
  const used = new Set(Object.values(mapping).filter(Boolean))
  const required = sheet.missing_required[domain] ?? []
  const stillMissing = required.filter((f) => !mapping[f])
  const recognised = file.source_system === 'dhis2' || file.source_system === 'msupply'
  return (
    <div className="file-mapper">
      <div className="row-note">
        Sheet <b>{sheet.name}</b>: {sheet.row_count} rows, {sheet.columns.length} columns, looks like{' '}
        <b>{sheet.domain}</b>
        {sheet.system !== 'unknown' ? ` from ${sheet.system}` : ''}
        {sheet.vintage_from
          ? `, ${sheet.vintage_from}${sheet.vintage_to && sheet.vintage_to !== sheet.vintage_from ? ` to ${sheet.vintage_to}` : ''}`
          : ''}
        .{sheet.matched_saved ? ` Matches the saved mapping “${sheet.matched_saved}”.` : ''}
        {recognised && domain === 'Demand' ? ' Recognised export: no mapping needed, stage it directly.' : ''}
      </div>
      {!(recognised && domain === 'Demand') && (
        <div className="scroll-x" tabIndex={0}>
          <table className="grid mapper-grid">
            <thead>
              <tr>
                <th scope="col">Our column</th>
                <th scope="col">Their column</th>
                <th scope="col">Unit</th>
                <th scope="col">First value</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((field) => (
                <tr key={field}>
                  <th scope="row">
                    {field}
                    {required.includes(field) ? ' *' : ''}
                  </th>
                  <td>
                    <select
                      className="cell-input"
                      aria-label={`Column for ${field}`}
                      value={mapping[field] ?? ''}
                      disabled={locked}
                      onChange={(e) => setMapping((m) => ({ ...m, [field]: e.target.value || null }))}
                    >
                      <option value="">—</option>
                      {sheet.columns.map((c) => (
                        <option key={c} value={c} disabled={used.has(c) && mapping[field] !== c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    {mapping[field] && (
                      <input
                        className="cell-input"
                        aria-label={`Unit of ${mapping[field]}`}
                        placeholder="as the model"
                        value={units[mapping[field] as string] ?? ''}
                        disabled={locked}
                        onChange={(e) =>
                          setUnits((u) => ({ ...u, [mapping[field] as string]: e.target.value }))
                        }
                      />
                    )}
                  </td>
                  <td className="tiny dim">
                    {mapping[field] ? String(sample[mapping[field] as string] ?? '') : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!(recognised && domain === 'Demand') && (
        <div className="session-actions" style={{ marginTop: 6, flexWrap: 'wrap' }}>
          <span className="tiny dim">Keep beside the record for cross-checks:</span>
          {sheet.columns
            .filter((c) => !used.has(c))
            .map((c) => (
              <button
                type="button"
                key={c}
                className={`chip${aux.includes(c) ? ' on' : ''}`}
                aria-pressed={aux.includes(c)}
                disabled={locked}
                onClick={() => setAux((a) => (a.includes(c) ? a.filter((x) => x !== c) : [...a, c]))}
              >
                {c}
              </button>
            ))}
        </div>
      )}
      {!locked && (
        <div className="session-actions" style={{ marginTop: 8 }}>
          {!(recognised && domain === 'Demand') && (
            <>
              <input
                className="tag-input"
                style={{ maxWidth: 220 }}
                placeholder="Save mapping as…"
                value={saveAs}
                onChange={(e) => setSaveAs(e.target.value)}
                aria-label="Save the mapping under a name"
              />
              <button
                type="button"
                className="btn small"
                disabled={busy || stillMissing.length > 0}
                title={stillMissing.length ? `Still needs ${stillMissing.join(', ')}` : ''}
                onClick={() =>
                  act(
                    () =>
                      api.setOnboardingMapping(file.id, {
                        mapping,
                        sheet: sheet.name,
                        domain,
                        aux_columns: aux,
                        units,
                        save_as: saveAs.trim(),
                      }),
                    () => void refresh(),
                  )
                }
              >
                Confirm mapping
              </button>
            </>
          )}
          <button
            type="button"
            className="btn small primary"
            disabled={busy || (!(recognised && domain === 'Demand') && file.status === 'profiled')}
            title={
              file.status === 'profiled' && !(recognised && domain === 'Demand')
                ? 'Confirm the mapping first'
                : ''
            }
            onClick={() =>
              act(
                () => api.stageOnboardingFile(file.id),
                (out) => {
                  setCounts(out.counts)
                  void refresh()
                },
              )
            }
          >
            Stage {file.status === 'staged' ? 'again' : ''}
          </button>
          {stillMissing.length > 0 && <span className="tiny warn-text">Needs {stillMissing.join(', ')}</span>}
        </div>
      )}
      {counts && (
        <p className="row-note" style={{ marginTop: 6 }}>
          {counts.rows} rows read, {counts.staged} staged
          {counts.matched
            ? `, ${counts.auto_accepted} facilities matched on their own, ${counts.pending} for the arbiter, ${counts.new} new`
            : ''}
          {counts.conflicts ? `, ${counts.conflicts} conflicts with another source` : ''}
          {counts.converted ? `, ${counts.converted} values converted by the pack` : ''}
          {counts.unresolved ? `, ${counts.unresolved} rows refer to nothing known` : ''}.
        </p>
      )}
    </div>
  )
}

// --- 3. facilities ---------------------------------------------------------------------------

function FacilitiesStep({
  run,
  countryId,
  busy,
  act,
  refresh,
}: {
  run: OnboardingRun
  countryId: number
  busy: boolean
  act: Act
  refresh: () => Promise<void>
}) {
  const [matches, setMatches] = useState<OnboardingMatch[]>([])
  const [filter, setFilter] = useState<'pending' | 'all'>('pending')
  const load = useCallback(() => {
    api
      .onboardingMatches(run.id, filter === 'pending' ? 'pending' : undefined)
      .then(setMatches)
      .catch(() => setMatches([]))
  }, [run.id, filter])
  useEffect(load, [load])
  const m = run.matches
  return (
    <div className="section">
      <p className="lever-note">
        No list is authoritative unless the pack names one. Every row is scored against the facilities already
        known, with the reasons; only a score above the pack's threshold is accepted without a person. The
        rest is the arbiter's.
      </p>
      <div className="kpi-grid" style={{ padding: 0, marginBottom: 8 }}>
        <div className="kpi">
          <label>Matched on their own</label>
          <b>{m.auto_accepted}</b>
        </div>
        <div className="kpi">
          <label>Decided by the arbiter</label>
          <b>{m.accepted}</b>
        </div>
        <div className="kpi">
          <label>Only one source knows</label>
          <b>{m.new}</b>
        </div>
        <div className="kpi">
          <label>Waiting for the arbiter</label>
          <b>{m.pending}</b>
        </div>
      </div>
      <div className="session-actions" style={{ marginBottom: 8 }}>
        <button
          type="button"
          className={`chip${filter === 'pending' ? ' on' : ''}`}
          aria-pressed={filter === 'pending'}
          onClick={() => setFilter('pending')}
        >
          Pending
        </button>
        <button
          type="button"
          className={`chip${filter === 'all' ? ' on' : ''}`}
          aria-pressed={filter === 'all'}
          onClick={() => setFilter('all')}
        >
          All
        </button>
        <a className="btn small ghost" href={api.crosswalkCsvUrl(countryId)}>
          Download the crosswalk (.csv)
        </a>
      </div>
      {matches.length === 0 && (
        <p className="row-note">
          {filter === 'pending' ? 'Nothing waiting for the arbiter.' : 'No facilities staged yet.'}
        </p>
      )}
      <ul className="match-list">
        {matches.slice(0, 80).map((match) => (
          <li key={match.id} className="match-card">
            <div>
              <b>{match.source_name || match.source_key}</b>{' '}
              <span className="tiny dim">{match.source_key}</span>{' '}
              <span
                className={`pill ${match.decision === 'pending' ? 'warn' : match.decision === 'new' ? 'info' : 'good'}`}
              >
                {match.decision.replace('_', ' ')}
              </span>
              {match.canonical_code && match.decision !== 'pending' && (
                <span className="tiny dim"> → {match.canonical_code}</span>
              )}
            </div>
            <div className="tiny dim">
              {String(match.record.admin1 ?? '')}
              {match.record.admin2 ? ` › ${match.record.admin2}` : ''} · confidence{' '}
              {match.confidence.toFixed(2)} · {match.reason}
            </div>
            {match.decision === 'pending' && (
              <div className="session-actions" style={{ marginTop: 4, flexWrap: 'wrap' }}>
                {match.candidates.map((c) => (
                  <button
                    type="button"
                    key={c.code}
                    className="btn small"
                    disabled={busy || !getAuthor()}
                    title={c.reasons.join('; ')}
                    onClick={() =>
                      act(
                        () => api.decideOnboardingMatch(match.id, { choice: c.code }),
                        () => {
                          load()
                          void refresh()
                        },
                      )
                    }
                  >
                    {c.name} · {c.score.toFixed(2)}
                  </button>
                ))}
                <button
                  type="button"
                  className="btn small ghost"
                  disabled={busy || !getAuthor()}
                  onClick={() =>
                    act(
                      () => api.decideOnboardingMatch(match.id, { choice: '__new__' }),
                      () => {
                        load()
                        void refresh()
                      },
                    )
                  }
                >
                  A facility only this source knows
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

// --- 4. checks and estimates ---------------------------------------------------------------

function ChecksStep({
  run,
  busy,
  act,
  refresh,
}: {
  run: OnboardingRun
  busy: boolean
  act: Act
  refresh: () => Promise<void>
}) {
  const checks = run.summary.checks as
    | {
        issues: number
        errors: number
        warnings: number
        baseline_total?: Record<string, { known: number; staged: number; gap: number }> | null
      }
    | undefined
  const estimates = run.summary.estimates as
    | { proposed: number; unfillable: number; by_method: Record<string, number> }
    | undefined
  const [records, setRecords] = useState<OnboardingRecord[] | null>(null)
  useEffect(() => {
    api
      .onboardingRecords(run.id, { status: 'blocked', limit: 50 })
      .then((r) => setRecords(r.items))
      .catch(() => setRecords(null))
  }, [run.id, run.updated_at])
  return (
    <div className="section">
      <p className="lever-note">
        Core checks that hold in any country, judged against this country's own figures; absolute ranges from
        the pack only. Gaps are filled by a named method or left open and flagged. Every flag and every
        estimate becomes a review item.
      </p>
      <div className="session-actions">
        <button
          type="button"
          className="btn small primary"
          disabled={busy}
          onClick={() =>
            act(
              () => api.runOnboardingChecks(run.id),
              () => void refresh(),
            )
          }
        >
          Run the checks
        </button>
        <button
          type="button"
          className="btn small"
          disabled={busy}
          onClick={() =>
            act(
              () => api.runOnboardingEstimates(run.id),
              () => void refresh(),
            )
          }
        >
          Propose estimates for the gaps
        </button>
      </div>
      {checks && (
        <p className="row-note" style={{ marginTop: 8 }}>
          <b>{checks.issues}</b> flags: {checks.errors} outside the pack's ranges or unresolved,{' '}
          {checks.warnings} worth a look.
          {checks.baseline_total &&
            Object.entries(checks.baseline_total).map(([k, v]) => (
              <span key={k}>
                {' '}
                {k.replace(/_/g, ' ')}: staged {v.staged.toLocaleString()} against a known{' '}
                {v.known.toLocaleString()} ({(v.gap * 100).toFixed(1)}%).
              </span>
            ))}
        </p>
      )}
      {estimates && (
        <p className="row-note">
          <b>{estimates.proposed}</b> estimates proposed
          {Object.keys(estimates.by_method).length
            ? ` (${Object.entries(estimates.by_method)
                .map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`)
                .join(', ')})`
            : ''}
          ; {estimates.unfillable} gaps no method can fill. Each estimate is directional, not for budgeting,
          and is listed on its own in Review.
        </p>
      )}
      {records && records.length > 0 && (
        <>
          <h4 className="tiny dim" style={{ marginTop: 10 }}>
            Blocked records (first {records.length})
          </h4>
          <ul className="tiny">
            {records.map((r) => (
              <li key={r.id}>
                <b>{r.label || r.key}</b> · {r.domain} ·{' '}
                {Object.entries(r.fields)
                  .filter(([, f]) => f.class === 'missing')
                  .map(([name, f]) => `${name}: ${f.reason}`)
                  .join('; ') || r.issues.map((i) => i.message).join('; ')}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

// --- 5. review ------------------------------------------------------------------------------

function ReviewStep({
  run,
  busy,
  act,
  refresh,
}: {
  run: OnboardingRun
  busy: boolean
  act: Act
  refresh: () => Promise<void>
}) {
  const [kind, setKind] = useState<string>('')
  const [queue, setQueue] = useState<{
    total: number
    by_kind: Record<string, number>
    by_confidence: Record<string, number>
    items: OnboardingItem[]
  } | null>(null)
  const [values, setValues] = useState<Record<number, string>>({})
  const load = useCallback(() => {
    api
      .onboardingQueue(run.id, { kind: kind || undefined, limit: 60 })
      .then(setQueue)
      .catch(() => setQueue(null))
  }, [run.id, kind])
  useEffect(load, [load])
  const decide = (item: OnboardingItem, decision: string, choice = '', value?: unknown) =>
    act(
      () => api.decideOnboardingItem(item.id, { decision, choice, value, comment: '' }),
      () => {
        load()
        void refresh()
      },
    )
  const bulkable =
    queue?.items
      .filter((i) => (i.kind === 'estimate' || i.kind === 'anomaly') && i.confidence !== 'low')
      .map((i) => i.id) ?? []
  return (
    <div className="section">
      <p className="lever-note">
        Sorted by how much each decision moves the result. Low-confidence items are decided one at a time; the
        rest can be accepted together once you have read them. Every decision is recorded with your name.
      </p>
      {queue && (
        <div className="session-actions" style={{ flexWrap: 'wrap', marginBottom: 8 }}>
          <button
            type="button"
            className={`chip${kind === '' ? ' on' : ''}`}
            aria-pressed={kind === ''}
            onClick={() => setKind('')}
          >
            All {queue.total}
          </button>
          {Object.entries(queue.by_kind).map(([k, n]) => (
            <button
              type="button"
              key={k}
              className={`chip${kind === k ? ' on' : ''}`}
              aria-pressed={kind === k}
              onClick={() => setKind(k)}
            >
              {k} {n}
            </button>
          ))}
          <span className="tiny dim">
            {Object.entries(queue.by_confidence)
              .map(([c, n]) => `${n} ${c}`)
              .join(' · ')}
          </span>
          {bulkable.length > 0 && (
            <button
              type="button"
              className="btn small"
              disabled={busy || !getAuthor()}
              onClick={() =>
                act(
                  () => api.bulkOnboardingDecide(run.id, { item_ids: bulkable, decision: 'accept' }),
                  () => {
                    load()
                    void refresh()
                  },
                )
              }
            >
              Accept the {bulkable.length} shown that are not low confidence
            </button>
          )}
        </div>
      )}
      {queue && queue.items.length === 0 && (
        <p className="row-note">Nothing pending{kind ? ` of kind ${kind}` : ''}.</p>
      )}
      <ul className="queue">
        {queue?.items.map((item) => (
          <li key={item.id} className={`queue-item ${item.confidence}`}>
            <div className="queue-head">
              <span
                className={`pill ${item.confidence === 'low' ? 'bad' : item.confidence === 'medium' ? 'warn' : 'good'}`}
              >
                {item.kind}
              </span>
              <b>{item.title}</b>
              <span className="tiny dim">
                {item.confidence} confidence · impact{' '}
                {item.impact >= 1000 ? Math.round(item.impact).toLocaleString() : item.impact.toFixed(1)} · by{' '}
                {item.proposed_by}
              </span>
            </div>
            {item.detail && <div className="tiny">{item.detail}</div>}
            <div className="session-actions" style={{ marginTop: 4, flexWrap: 'wrap' }}>
              {item.kind === 'estimate' && (
                <>
                  <button
                    type="button"
                    className="btn small primary"
                    disabled={busy || !getAuthor()}
                    onClick={() => decide(item, 'accept')}
                  >
                    Use this estimate
                  </button>
                  <button
                    type="button"
                    className="btn small ghost"
                    disabled={busy || !getAuthor()}
                    onClick={() => decide(item, 'reject')}
                  >
                    Leave it missing
                  </button>
                </>
              )}
              {item.kind === 'conflict' &&
                item.options.map((o) => (
                  <button
                    type="button"
                    key={String(o.index)}
                    className="btn small"
                    disabled={busy || !getAuthor()}
                    onClick={() => decide(item, 'choose', String(o.index))}
                  >
                    {o.label}
                  </button>
                ))}
              {(item.kind === 'anomaly' ||
                item.kind === 'missing' ||
                item.kind === 'unit' ||
                item.kind === 'question') && (
                <>
                  {item.kind === 'anomaly' && (
                    <>
                      <button
                        type="button"
                        className="btn small"
                        disabled={busy || !getAuthor()}
                        onClick={() => decide(item, 'accept', 'keep')}
                      >
                        The value is right
                      </button>
                      <button
                        type="button"
                        className="btn small ghost"
                        disabled={busy || !getAuthor()}
                        onClick={() => decide(item, 'reject', 'drop')}
                      >
                        Mark it missing
                      </button>
                    </>
                  )}
                  <input
                    className="tag-input"
                    style={{ maxWidth: 140 }}
                    aria-label={`Correct value for ${item.title}`}
                    placeholder="right value"
                    value={values[item.id] ?? ''}
                    onChange={(e) => setValues((v) => ({ ...v, [item.id]: e.target.value }))}
                  />
                  <button
                    type="button"
                    className="btn small"
                    disabled={busy || !getAuthor() || !(values[item.id] ?? '').trim()}
                    onClick={() =>
                      decide(item, item.kind === 'question' ? 'answer' : 'correct', '', values[item.id])
                    }
                  >
                    {item.kind === 'question' ? 'Record the answer' : 'Type the right value'}
                  </button>
                </>
              )}
              {item.kind === 'mapping' && (
                <span className="tiny dim">Confirm the mapping on the file, in Files.</span>
              )}
              {item.kind === 'match' && <span className="tiny dim">Decide it in Facilities.</span>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

// --- 6. sign-off -----------------------------------------------------------------------------

function SignOffStep({
  run,
  busy,
  act,
  refresh,
  onLoaded,
}: {
  run: OnboardingRun
  busy: boolean
  act: Act
  refresh: () => Promise<void>
  onLoaded: () => void
}) {
  const [report, setReport] = useState<OnboardingReport | null>(null)
  const [comment, setComment] = useState('')
  useEffect(() => {
    api
      .onboardingReport(run.id)
      .then(setReport)
      .catch(() => setReport(null))
  }, [run.id, run.updated_at, run.status])
  if (!report) return <div className="section row-note">Loading the report…</div>
  const cov = report.coverage
  const total = Object.values(cov.totals).reduce((a, b) => a + b, 0)
  return (
    <div className="section">
      <p className="lever-note">
        The validation report: coverage by class, every estimate, every conflict, every flag and its
        resolution, the vintage of every source. A named approver who is not the preparer signs it; loading
        the model is the step after.
      </p>
      <div className="scroll-x" tabIndex={0}>
        <table className="grid" style={{ minWidth: 0 }}>
          <caption className="visually-hidden">Coverage by provenance class</caption>
          <thead>
            <tr>
              <th scope="col">Class</th>
              <th scope="col" className="n">
                Values
              </th>
              <th scope="col" className="n">
                Share
              </th>
            </tr>
          </thead>
          <tbody>
            {CLASS_ORDER.map((c) => (
              <tr
                key={c}
                className={cov.totals[c] && (c === 'illustrative' || c === 'missing') ? 'blocking' : ''}
              >
                <th scope="row">{c}</th>
                <td className="n">{cov.totals[c] ?? 0}</td>
                <td className="n">{total ? `${(((cov.totals[c] ?? 0) / total) * 100).toFixed(1)}%` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="row-note" style={{ marginTop: 6 }}>
        {report.records.total} records from {report.sources.length} files · {report.estimates.length}{' '}
        estimates · {report.conflicts.filter((c) => c.open).length} open of {report.conflicts.length}{' '}
        conflicts · {report.anomalies.filter((a) => a.resolution === 'pending').length} open of{' '}
        {report.anomalies.length} flags · {report.pending.length} pending decisions ·{' '}
        {report.reviewers.decisions} decisions by {report.reviewers.reviewers.join(', ') || 'nobody yet'}.
      </p>
      <ul className="tiny">
        {report.sources.map((s) => (
          <li key={s.id}>
            {s.filename}: {s.system}, {s.domain}, vintage {s.vintage_from || '?'}
            {s.vintage_to && s.vintage_to !== s.vintage_from ? ` to ${s.vintage_to}` : ''}, uploaded by{' '}
            {s.uploader}
          </li>
        ))}
      </ul>
      {report.blockers.length > 0 ? (
        <div className="callout warn">
          <h4>Sign-off is blocked</h4>
          <ul style={{ margin: '4px 0 0 16px' }}>
            {report.blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </div>
      ) : (
        report.can_sign_off && (
          <div className="callout good">
            <h4>Nothing blocks sign-off</h4>
            No illustrative or missing value, no open conflict, no pending decision.
          </div>
        )
      )}
      {report.approval && (
        <p className="row-note">
          <b>{report.approval.decision}</b> by {report.approval.approver}
          {report.approval.comment ? `: ${report.approval.comment}` : ''}
        </p>
      )}
      <div className="session-actions" style={{ marginTop: 8, flexWrap: 'wrap' }}>
        <a className="btn small ghost" href={api.onboardingExportUrl(run.id)}>
          Download the workbook with its provenance sheet
        </a>
        {(run.status === 'open' || run.status === 'in_review') && (
          <>
            <input
              className="tag-input"
              style={{ maxWidth: 240 }}
              placeholder="Approver's comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              aria-label="Approver's comment"
            />
            <button
              type="button"
              className="btn small primary"
              disabled={busy || !getAuthor() || !report.can_sign_off}
              title={getAuthor() === run.preparer ? 'The preparer cannot approve their own dataset' : ''}
              onClick={() =>
                act(
                  () => api.signOffOnboarding(run.id, { decision: 'approved', comment }),
                  () => void refresh(),
                )
              }
            >
              Approve as {getAuthor() || '(sign your name)'}
            </button>
            <button
              type="button"
              className="btn small ghost"
              disabled={busy || !getAuthor()}
              onClick={() =>
                act(
                  () => api.signOffOnboarding(run.id, { decision: 'rejected', comment }),
                  () => void refresh(),
                )
              }
            >
              Reject
            </button>
          </>
        )}
        {run.status === 'signed_off' && (
          <button
            type="button"
            className="btn small primary"
            disabled={busy || !getAuthor()}
            onClick={() =>
              act(
                () => api.loadOnboarding(run.id),
                () => {
                  void refresh()
                  onLoaded()
                },
              )
            }
          >
            Load into the model
          </button>
        )}
        {run.status === 'loaded' && <span className="pill good">Loaded into the model</span>}
      </div>
    </div>
  )
}
