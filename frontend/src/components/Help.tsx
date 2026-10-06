import { useEffect, useId, useRef, useState } from 'react'

/**
 * "What is this for?" — a small round button that explains the feature beside it.
 *
 * Hover or focus shows the explanation; click or Enter pins it; Escape or clicking
 * elsewhere dismisses it. The button names itself for screen readers and points at
 * the text with aria-describedby, so the help is read where the control is, not
 * discovered somewhere else. The text comes from help.ts, which is writing, not code.
 */
export function Help({ text, label, inline = false }: { text: string; label?: string; inline?: boolean }) {
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const id = useId()
  const ref = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!pinned) return
    const away = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setPinned(false)
        setOpen(false)
      }
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPinned(false)
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [pinned])

  const visible = open || pinned
  return (
    <span className={`help${inline ? ' inline' : ''}`} ref={ref}>
      <button
        type="button"
        className="help-btn"
        aria-label={label ? `What is ${label} for?` : 'What is this for?'}
        aria-describedby={visible ? id : undefined}
        aria-expanded={visible}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          if (!pinned) setOpen(false)
        }}
        onClick={() => setPinned((v) => !v)}
      >
        ?
      </button>
      {visible && (
        <span role="tooltip" id={id} className="help-pop">
          {text}
        </span>
      )}
    </span>
  )
}
