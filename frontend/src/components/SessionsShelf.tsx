import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Help } from './Help'
import { HELP } from '../help'
import type { SessionDiff, SessionShelf, WorkSession } from '../types'

/**
 * The Sessions shelf: a named, complete save of everything on screen.
 *
 * "PNG baseline · 12 Sep · Ulrich" is the save file everyone already understands.
 * Open one and the map, data, scenarios and results are exactly as saved; the work you
 * were in the middle of is kept as a draft first, so opening something older can never
 * lose anything. Compare reads the difference in sentences rather than in a spreadsheet.
 */
export function SessionsShelf({
  countryId,
  refreshKey,
  onOpened,
  onError,
}: {
  countryId: number
  /** Bumped by the app whenever the working state is reloaded, so "changes since" stays honest. */
  refreshKey: number
  onOpened: (message: string) => Promise<void> | void
  onError: (message: string) => void
}) {
  const [shelf, setShelf] = useState<SessionShelf | null>(null)
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [diff, setDiff] = useState<{ id: number; data: SessionDiff } | null>(null)
  const [showAll, setShowAll] = useState(false)

  const reload = useCallback(() => {
    api
      .sessions(countryId)
      .then(setShelf)
      .catch(() => setShelf(null))
  }, [countryId])

  useEffect(() => {
    reload()
  }, [reload, refreshKey])

  async function save() {
    if (!name.trim()) return
    setBusy(true)
    try {
      const saved = await api.saveSession(countryId, {
        name: name.trim(),
        note: note.trim(),
      })
      setMessage(`Saved as “${saved.name}”. You are now working from it.`)
      setName('')
      setNote('')
      setSaving(false)
      reload()
    } catch (e) {
      onError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function open(session: WorkSession) {
    const unsaved = shelf?.current ? shelf.current.changes_since > 0 : true
    const warning = unsaved ? ' The work you are in now is kept as a draft first.' : ''
    if (
      !window.confirm(
        `Open “${session.name}”? The map, data, scenarios and results become exactly as saved.${warning}`,
      )
    )
      return
    setBusy(true)
    try {
      const outcome = await api.openSession(session.id)
      const kept = outcome.draft ? ` Your previous work is kept as “${outcome.draft.name}”.` : ''
      setDiff(null)
      await onOpened(
        `Opened “${session.name}”: ${outcome.restored.nodes} facilities and stores, ${outcome.restored.scenarios} scenarios, ${outcome.restored.results} results.${kept}`,
      )
      reload()
    } catch (e) {
      onError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function compare(session: WorkSession) {
    if (diff?.id === session.id) {
      setDiff(null)
      return
    }
    try {
      setDiff({
        id: session.id,
        data: await api.sessionDiff(session.id, 'current'),
      })
    } catch (e) {
      onError(String(e))
    }
  }

  async function nameDraft(session: WorkSession) {
    const chosen = window.prompt(
      'Name this draft to keep it as a saved session:',
      session.name.replace(/^Draft (of|before) /, ''),
    )
    if (!chosen || !chosen.trim()) return
    try {
      await api.renameSession(session.id, { name: chosen.trim() })
      reload()
    } catch (e) {
      onError(String(e))
    }
  }

  async function remove(session: WorkSession) {
    if (!window.confirm(`Delete “${session.name}”? The saved state cannot be recovered afterwards.`)) return
    try {
      await api.deleteSession(session.id)
      if (diff?.id === session.id) setDiff(null)
      reload()
    } catch (e) {
      onError(String(e))
    }
  }

  const sessions = shelf?.sessions ?? []
  const visible = showAll ? sessions : sessions.slice(0, 4)
  const current = shelf?.current ?? null

  return (
    <div className="section sessions" data-tour="sessions">
      <h3>
        Sessions
        <Help text={HELP.sessions} label="sessions" />
      </h3>

      <div className="session-current" role="status">
        {current ? (
          <>
            Working from <b>{current.name}</b>
            {current.kind === 'draft' ? ' (a draft)' : ''} · {when(current.created_at)} ·{' '}
            {current.author_claim} ·{' '}
            {current.changes_since === 0
              ? 'nothing changed since'
              : `${current.changes_since} change${current.changes_since === 1 ? '' : 's'} since`}
          </>
        ) : (
          <>
            Nothing saved yet. Saving names the working state so it can be reopened exactly, later, by anyone.
          </>
        )}
      </div>

      {!saving ? (
        <div className="session-actions">
          <button type="button" className="btn small primary" onClick={() => setSaving(true)} disabled={busy}>
            Save…
          </button>
        </div>
      ) : (
        <form
          className="session-form"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <label>
            <span className="visually-hidden">Session name</span>
            <input
              className="tag-input"
              value={name}
              autoFocus
              placeholder="Name, e.g. PNG baseline"
              maxLength={160}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            <span className="visually-hidden">Note</span>
            <input
              className="tag-input"
              value={note}
              placeholder="One line on what this is (optional)"
              maxLength={400}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          <div className="session-actions">
            <button type="submit" className="btn small primary" disabled={busy || !name.trim()}>
              Save session
            </button>
            <button type="button" className="btn small ghost" onClick={() => setSaving(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {message && (
        <div className="lever-note editor-message" role="status">
          {message}
        </div>
      )}

      {visible.length > 0 && (
        <ul className="session-list">
          {visible.map((session) => (
            <li key={session.id} className={`session-row${session.is_current ? ' current' : ''}`}>
              <div className="session-name">
                <span>{session.name}</span>
                {session.kind === 'draft' && <span className="pill">draft</span>}
                {session.is_current && <span className="pill info">working from</span>}
              </div>
              <div className="row-note">
                {when(session.created_at)} · {session.author_claim} · {session.summary.scenarios ?? 0}{' '}
                scenarios, {session.summary.results ?? 0} results
                {session.note ? ` · ${session.note}` : ''}
              </div>
              <div className="session-buttons">
                <button type="button" className="btn small" onClick={() => open(session)} disabled={busy}>
                  Open
                </button>
                <button
                  type="button"
                  className="btn small ghost"
                  aria-pressed={diff?.id === session.id}
                  onClick={() => compare(session)}
                >
                  {diff?.id === session.id ? 'Hide' : 'Compare'}
                </button>
                {session.kind === 'draft' && (
                  <button type="button" className="btn small ghost" onClick={() => nameDraft(session)}>
                    Name…
                  </button>
                )}
                <button
                  type="button"
                  className="btn small ghost"
                  onClick={() => remove(session)}
                  aria-label={`Delete session ${session.name}`}
                >
                  Delete
                </button>
              </div>
              {diff?.id === session.id && (
                <div
                  className="session-diff"
                  role="region"
                  aria-label={`Difference from ${diff.data.from} to ${diff.data.to}`}
                >
                  <div className="tiny dim">
                    From <b>{diff.data.from}</b> to <b>{diff.data.to}</b>
                  </div>
                  <ul>
                    {diff.data.sentences.map((sentence) => (
                      <li key={sentence}>{sentence}</li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {sessions.length > 4 && (
        <button type="button" className="btn small ghost" onClick={() => setShowAll((value) => !value)}>
          {showAll ? 'Show fewer' : `Show all ${sessions.length}`}
        </button>
      )}
    </div>
  )
}

function when(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return date.toLocaleDateString(
    undefined,
    sameYear ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' },
  )
}
