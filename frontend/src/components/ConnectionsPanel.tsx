import { useEffect, useState } from 'react'
import { api, connectorApi } from '../api'
import type { Connection, ConnectorSpec, SyncPreview } from '../types'
import { exact } from '../format'

/**
 * Live LMIS connections.
 *
 * The panel is arranged to make the sequence unavoidable: configure, test, preview,
 * then apply. There is no button that reaches out and rewrites a national facility
 * list, and the apply button stays disabled until somebody has looked at what would
 * change.
 */
export function ConnectionsPanel({
  countryId,
  onApplied,
}: {
  countryId: number
  onApplied: () => void
}) {
  const [systems, setSystems] = useState<ConnectorSpec[]>([])
  const [note, setNote] = useState('')
  const [connections, setConnections] = useState<Connection[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [preview, setPreview] = useState<SyncPreview | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const selected = connections.find((c) => c.id === selectedId) ?? null
  const spec = systems.find((s) => s.system === selected?.system) ?? null

  const reload = () =>
    connectorApi
      .list(countryId)
      .then(setConnections)
      .catch((e) => setError(String(e)))

  useEffect(() => {
    connectorApi
      .systems()
      .then((r) => {
        setSystems(r.systems)
        setNote(r.note)
      })
      .catch((e) => setError(String(e)))
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countryId])

  async function run<T>(label: string, work: () => Promise<T>): Promise<T | null> {
    setBusy(label)
    setError(null)
    try {
      return await work()
    } catch (e) {
      setError(String(e))
      return null
    } finally {
      setBusy(null)
    }
  }

  async function testConnection(id: number) {
    await run('test', async () => {
      await connectorApi.test(id)
      await reload()
    })
  }

  async function previewSync(id: number) {
    setPreview(null)
    setMessage(null)
    const result = await run('preview', () => connectorApi.preview(id))
    if (result) setPreview(result)
  }

  async function applySync() {
    if (!preview || !selected) return
    await run('apply', async () => {
      const outcome = await api.commitImport(preview.batch_id, false)
      await connectorApi.recordSync(selected.id, outcome.counts as Record<string, number>)
      setMessage(
        `Applied. ${outcome.counts.nodes_created ?? 0} facilities created, ` +
          `${outcome.counts.nodes_updated ?? 0} updated, ${outcome.counts.demand ?? 0} demand rows. ` +
          `Lanes were not touched. Re-run your scenarios — until you do, their results ` +
          `describe the previous data.`,
      )
      setPreview(null)
      await reload()
      onApplied()
    })
  }

  return (
    <div>
      <div className="callout">
        <h4>Live connections</h4>
        {note || 'Read-only pulls from the systems that already hold this country’s data.'}
      </div>

      <div className="section">
        <h3>
          Connections
          <button
            className="btn small ghost"
            style={{ float: 'right', marginTop: -3 }}
            onClick={() => setAdding(!adding)}
          >
            {adding ? 'cancel' : '+ add'}
          </button>
        </h3>

        {connections.length === 0 && !adding && (
          <div className="lever-note">
            None configured. Until one is, this country’s data comes in through the Excel
            importer, which is a first-class path rather than a fallback.
          </div>
        )}

        {connections.map((connection) => {
          const system = systems.find((s) => s.system === connection.system)
          return (
            <div
              key={connection.id}
              className={`connection${connection.id === selectedId ? ' active' : ''}`}
              onClick={() => {
                setSelectedId(connection.id)
                setPreview(null)
              }}
            >
              <div className="connection-head">
                <b>{connection.name}</b>
                <span className="pill">{system?.label ?? connection.system}</span>
                {connection.last_test_ok === true && <span className="pill good">tested</span>}
                {connection.last_test_ok === false && <span className="pill bad">failing</span>}
              </div>
              <div className="connection-desc">
                {connection.base_url || 'no URL yet'} · credential{' '}
                {connection.secret_source === 'environment'
                  ? `from $${connection.secret_env}`
                  : connection.secret_source === 'stored'
                    ? 'stored here'
                    : 'not set'}
                {connection.last_sync_at && (
                  <> · last applied {new Date(connection.last_sync_at).toLocaleString()}</>
                )}
              </div>

              {connection.id === selectedId && (
                <div className="connection-actions" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="btn small"
                    disabled={busy !== null}
                    onClick={() => void testConnection(connection.id)}
                  >
                    {busy === 'test' ? <span className="spinner" /> : 'Test'}
                  </button>
                  <button
                    className="btn small primary"
                    disabled={busy !== null}
                    onClick={() => void previewSync(connection.id)}
                  >
                    {busy === 'preview' ? <span className="spinner" /> : 'Preview sync'}
                  </button>
                  <button
                    className="btn small ghost"
                    onClick={() =>
                      window.confirm('Delete this connection?') &&
                      void run('delete', async () => {
                        await connectorApi.remove(connection.id)
                        setSelectedId(null)
                        await reload()
                      })
                    }
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
          )
        })}

        {adding && (
          <NewConnectionForm
            systems={systems}
            onCancel={() => setAdding(false)}
            onCreate={async (body) => {
              const created = await run('create', () => connectorApi.create(countryId, body))
              if (created) {
                setAdding(false)
                setSelectedId(created.id)
                await reload()
              }
            }}
          />
        )}
      </div>

      {error && <div className="callout bad">{error}</div>}
      {message && <div className="callout good">{message}</div>}

      {preview && <PreviewReport preview={preview} busy={busy} onApply={applySync} />}

      {selected?.last_test_detail && 'checks' in selected.last_test_detail && (
        <>
          <div className="section">
            <h3>Last connection test</h3>
            <div className="lever-note">
              Reported step by step. “Connection failed” cannot be acted on; “authenticated,
              but this account cannot read organisation units” can.
            </div>
          </div>
          {selected.last_test_detail.checks.map((check) => (
            <div className={`issue ${check.ok ? 'info' : 'error'}`} key={check.name}>
              <div className="issue-head">
                <span className={`pill ${check.ok ? 'good' : 'bad'}`}>{check.ok ? 'ok' : 'no'}</span>
                <b style={{ fontSize: 11.5 }}>{check.name}</b>
              </div>
              {check.detail && <p className="suggestion">{check.detail}</p>}
            </div>
          ))}
        </>
      )}

      {selected && spec && (
        <ConfigEditor
          key={selected.id}
          connection={selected}
          spec={spec}
          onSave={async (config) => {
            await run('config', async () => {
              await connectorApi.update(selected.id, { config })
              await reload()
              setMessage('Settings saved. Test the connection again before previewing.')
            })
          }}
        />
      )}

      {spec && !spec.verified_against_live_instance && (
        <div className="callout">
          <h4>Not yet confirmed against a live {spec.label} server</h4>
          The endpoints follow the published API and are tested against recorded response
          shapes, but nobody has run this against a ministry’s own instance. Point it at the
          real server early — every setting on this connection is editable, so a version
          difference is a form field rather than a release.{' '}
          <a href={spec.docs_url} target="_blank" rel="noreferrer">
            API documentation
          </a>
        </div>
      )}

    </div>
  )
}

function PreviewReport({
  preview,
  busy,
  onApply,
}: {
  preview: SyncPreview
  busy: string | null
  onApply: () => void
}) {
  const [tab, setTab] = useState<'changes' | 'issues' | 'notes'>('changes')
  const summary = preview.reconciliation.summary

  return (
    <>
      <div className={`callout ${preview.blocking ? 'bad' : 'good'}`}>
        <h4>{preview.reconciliation.headline}</h4>
        {preview.headline}
        <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="pill">{summary.matched} matched</span>
          <span className="pill info">{summary.new} new</span>
          {summary.moved > 0 && <span className="pill warn">{summary.moved} moved</span>}
          {summary.renamed > 0 && <span className="pill warn">{summary.renamed} renamed</span>}
          {preview.counts.error > 0 && <span className="pill bad">{preview.counts.error} errors</span>}
          <button className="btn small primary" disabled={preview.blocking || busy !== null} onClick={onApply}>
            {busy === 'apply' ? <span className="spinner" /> : 'Apply this sync'}
          </button>
        </div>
        <div className="tiny dim" style={{ marginTop: 7 }}>
          {preview.commit_note}
        </div>
      </div>

      <div className="section">
        <div className="chips">
          {(['changes', 'issues', 'notes'] as const).map((key) => (
            <span key={key} className={`chip${tab === key ? ' on' : ''}`} onClick={() => setTab(key)}>
              {key === 'issues' ? `issues (${preview.issues.length})` : key}
            </span>
          ))}
        </div>
      </div>

      {tab === 'changes' && (
        <table>
          <thead>
            <tr>
              <th>Facility</th>
              <th>Matched by</th>
              <th>Would change</th>
            </tr>
          </thead>
          <tbody>
            {preview.reconciliation.matched
              .filter((m) => Object.keys(m.changes).length > 0)
              .map((m) => (
                <tr key={m.incoming_code}>
                  <td>
                    {m.existing_name}
                    <div className="row-note">{m.existing_code}</div>
                  </td>
                  <td className="tiny dim">{m.method.replace(/_/g, ' ')}</td>
                  <td className="tiny">
                    {m.distance_moved_km !== null && m.distance_moved_km > 0.05 && (
                      <div style={{ color: 'var(--warn)' }}>moves {m.distance_moved_km} km</div>
                    )}
                    {'name' in m.changes && (
                      <div>
                        renamed to <b>{String(m.changes.name[1])}</b>
                      </div>
                    )}
                    {m.changes.lat && m.distance_moved_km === null && (
                      <div style={{ color: 'var(--good)' }}>gains a coordinate</div>
                    )}
                    {Object.keys(m.changes)
                      .filter((k) => !['name', 'lat', 'lon'].includes(k))
                      .map((k) => (
                        <div key={k}>
                          {k}: {String(m.changes[k][0])} → {String(m.changes[k][1])}
                        </div>
                      ))}
                  </td>
                </tr>
              ))}
            {preview.reconciliation.new.map((m) => (
              <tr key={`new-${m.incoming_code}`}>
                <td>
                  {m.incoming_name}
                  <div className="row-note">{m.incoming_code}</div>
                </td>
                <td>
                  <span className="pill info">new</span>
                </td>
                <td className="tiny dim">would be created</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {tab === 'issues' &&
        preview.issues.map((issue, index) => (
          <div className={`issue ${issue.severity}`} key={`${issue.code}-${index}`}>
            <div className="issue-head">
              <span
                className={`pill ${issue.severity === 'error' ? 'bad' : issue.severity === 'warning' ? 'warn' : 'info'}`}
              >
                {issue.severity}
              </span>
              <code>{issue.code}</code>
            </div>
            <p>{issue.message}</p>
            <p className="suggestion">{issue.suggestion}</p>
          </div>
        ))}

      {tab === 'notes' && (
        <>
          <div className="section">
            <h3>What the connector could not do</h3>
          </div>
          {preview.connector_warnings.length === 0 && (
            <div className="lever-note" style={{ padding: '0 14px' }}>
              Nothing to report.
            </div>
          )}
          {preview.connector_warnings.map((warning, index) => (
            <div className="issue warning" key={index}>
              <p>{warning}</p>
            </div>
          ))}
          <div className="callout">
            <h4>Pulled</h4>
            {Object.entries(preview.stats).map(([key, value]) => (
              <div key={key} className="tiny">
                {key.replace(/_/g, ' ')}: <b>{typeof value === 'number' ? exact(value) : String(value)}</b>
              </div>
            ))}
          </div>
          {preview.reconciliation.absent.length > 0 && (
            <div className="callout">
              <h4>In the model but not in this pull</h4>
              <div className="tiny dim">
                Not necessarily closed — a filter on the connection, or a facility this account
                cannot see, looks the same from here. Nothing is deleted either way.
              </div>
              <div className="tiny" style={{ marginTop: 6 }}>
                {preview.reconciliation.absent
                  .slice(0, 12)
                  .map((row) => row.name)
                  .join(', ')}
                {preview.reconciliation.truncated.absent > 0 &&
                  ` and ${preview.reconciliation.truncated.absent} more`}
              </div>
            </div>
          )}
        </>
      )}
    </>
  )
}

/**
 * Per-connection settings, generated from the connector's own config spec.
 *
 * This is where the "mapping is data, not code" claim is either true or a slogan.
 * A DHIS2 2.36 instance that names an endpoint differently, or a country whose
 * facilities sit at level 5 rather than 4, is fixed here — by the person in the room,
 * during the workshop — rather than by a release three weeks later.
 */
function ConfigEditor({
  connection,
  spec,
  onSave,
}: {
  connection: Connection
  spec: ConnectorSpec
  onSave: (config: Record<string, unknown>) => void
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})

  const current = (key: string): unknown =>
    key in connection.config ? connection.config[key] : spec.config_spec[key]?.default

  const asText = (value: unknown): string =>
    typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : String(value ?? '')

  const value = (key: string) => (key in draft ? draft[key] : asText(current(key)))
  const dirty = Object.keys(draft).length > 0

  function save() {
    const config: Record<string, unknown> = { ...connection.config }
    for (const [key, raw] of Object.entries(draft)) {
      const kind = spec.config_spec[key]?.kind
      if (kind === 'number') config[key] = Number(raw)
      else if (kind === 'boolean') config[key] = raw === 'true'
      else if (kind === 'map') {
        try {
          config[key] = raw.trim() ? JSON.parse(raw) : {}
        } catch {
          window.alert(`${key} is not valid JSON.`)
          return
        }
      } else config[key] = raw
    }
    setDraft({})
    onSave(config)
  }

  return (
    <div className="section">
      <h3>
        Settings
        <button
          className="btn small ghost"
          style={{ float: 'right', marginTop: -3 }}
          onClick={() => setOpen(!open)}
        >
          {open ? 'hide' : `${Object.keys(spec.config_spec).length} settings`}
        </button>
      </h3>

      {!open && (
        <div className="lever-note">
          Endpoints, field selectors and the product mapping. Every one is editable, so a
          version difference is a form field rather than a release.
        </div>
      )}

      {open && (
        <>
          {Object.entries(spec.config_spec).map(([key, entry]) => (
            <div className="lever" key={key}>
              <div className="lever-head">
                <label>{key.replace(/_/g, ' ')}</label>
                {key in connection.config && <span className="pill info">set</span>}
              </div>
              {entry.kind === 'boolean' ? (
                <select value={value(key)} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : entry.kind === 'map' || entry.kind === 'graphql' ? (
                <textarea
                  value={value(key)}
                  rows={entry.kind === 'graphql' ? 6 : 4}
                  spellCheck={false}
                  onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                  style={{
                    width: '100%',
                    background: 'var(--panel-3)',
                    border: '1px solid var(--line)',
                    borderRadius: 5,
                    padding: '5px 7px',
                    fontFamily: 'var(--mono)',
                    fontSize: 11,
                    resize: 'vertical',
                  }}
                />
              ) : (
                <input
                  value={value(key)}
                  onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                  style={{
                    width: '100%',
                    background: 'var(--panel-3)',
                    border: '1px solid var(--line)',
                    borderRadius: 5,
                    padding: '5px 7px',
                  }}
                />
              )}
              <div className="lever-note">{entry.help}</div>
            </div>
          ))}
          <button className="btn small primary" disabled={!dirty} onClick={save}>
            Save settings
          </button>
        </>
      )}
    </div>
  )
}

function NewConnectionForm({
  systems,
  onCreate,
  onCancel,
}: {
  systems: ConnectorSpec[]
  onCreate: (body: Record<string, unknown>) => void
  onCancel: () => void
}) {
  const [system, setSystem] = useState(systems[0]?.system ?? 'dhis2')
  const [name, setName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [username, setUsername] = useState('')
  const [secret, setSecret] = useState('')
  const [secretEnv, setSecretEnv] = useState('')
  const [authType, setAuthType] = useState('basic')

  const spec = systems.find((s) => s.system === system)

  return (
    <div className="connection active" style={{ cursor: 'default' }}>
      <div className="lever">
        <div className="lever-head">
          <label>System</label>
        </div>
        <select
          value={system}
          onChange={(e) => {
            setSystem(e.target.value)
            const next = systems.find((s) => s.system === e.target.value)
            setAuthType(next?.auth_types[0] ?? 'basic')
          }}
        >
          {systems.map((s) => (
            <option key={s.system} value={s.system}>
              {s.label}
            </option>
          ))}
        </select>
        {spec && <div className="lever-note">{spec.description}</div>}
      </div>

      <Field label="Name" value={name} onChange={setName} placeholder="NDoH DHIS2 (production)" />
      <Field label="Base URL" value={baseUrl} onChange={setBaseUrl} placeholder="https://dhis2.health.gov" />

      <div className="lever">
        <div className="lever-head">
          <label>Authentication</label>
        </div>
        <select value={authType} onChange={(e) => setAuthType(e.target.value)}>
          {(spec?.auth_types ?? ['basic']).map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      {authType === 'basic' && <Field label="Username" value={username} onChange={setUsername} />}
      <Field
        label="Credential environment variable"
        value={secretEnv}
        onChange={setSecretEnv}
        placeholder="DHIS2_TOKEN"
      />
      <div className="lever-note" style={{ marginTop: -6, marginBottom: 10 }}>
        Preferred: the credential stays in the process environment and never reaches the
        database or a backup of it. Leave blank only for a laptop during a workshop.
      </div>
      {!secretEnv && (
        <Field label="Credential (stored)" value={secret} onChange={setSecret} type="password" />
      )}

      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <button
          className="btn small primary"
          disabled={!name || !baseUrl}
          onClick={() =>
            onCreate({
              name,
              system,
              base_url: baseUrl,
              auth_type: authType,
              username,
              secret: secret || null,
              secret_env: secretEnv,
            })
          }
        >
          Create
        </button>
        <button className="btn small ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: string
}) {
  return (
    <div className="lever">
      <div className="lever-head">
        <label>{label}</label>
      </div>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: '100%',
          background: 'var(--panel-3)',
          border: '1px solid var(--line)',
          borderRadius: 5,
          padding: '5px 7px',
        }}
      />
    </div>
  )
}
