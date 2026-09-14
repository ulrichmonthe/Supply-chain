/*
 * A guided tour: a dimmed page, a lit-up control, and a card explaining it.
 *
 * The thing being explained stays visible — that is the whole point of cutting a hole
 * in the scrim rather than showing a slideshow of screenshots. Screenshots go stale
 * the week after they are taken; this points at the real control, in the real layout,
 * with the reader's own data behind it.
 *
 * Interaction outside the card is blocked while the tour is open. A tour step says
 * "this button runs the solver"; if the reader could press it mid-step the sentence
 * would be describing a screen that had already moved on. Steps that need the app in a
 * particular state put it there themselves, through `before`.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type React from 'react'

export type Placement = 'right' | 'left' | 'top' | 'bottom' | 'centre'

export type TourStep = {
  /** Stable id, used as the React key and in the progress announcement. */
  id: string
  title: string
  body: React.ReactNode
  /** CSS selector for the control being explained. Absent — or unmatched — centres the card. */
  target?: string
  placement?: Placement
  /** Breathing room between the control and the edge of the lit area, in pixels. */
  padding?: number
  /** Put the app into the state this step describes. Runs once, on entering the step. */
  before?: () => void
}

type Rect = { top: number; left: number; width: number; height: number }

const CARD_WIDTH = 348
const GAP = 14 // between the lit area and the card
const MARGIN = 12 // minimum distance from the edge of the viewport
const SHEET_BREAKPOINT = 820 // below this the card becomes a bottom sheet

const sameRect = (a: Rect | null, b: Rect | null) => {
  if (a === b) return true
  if (!a || !b) return false
  return (
    Math.abs(a.top - b.top) < 0.5 &&
    Math.abs(a.left - b.left) < 0.5 &&
    Math.abs(a.width - b.width) < 0.5 &&
    Math.abs(a.height - b.height) < 0.5
  )
}

/**
 * Where the card goes.
 *
 * Tries the step's preferred side first, then the others, and takes the first that
 * fits entirely on screen. Nothing fitting is normal on a small window, so the last
 * resort is a clamped position rather than a card with its buttons off the edge.
 */
function place(
  target: Rect | null,
  card: { width: number; height: number },
  prefer: Placement,
): { left: number; top: number; arrow: Placement | null } {
  const vw = window.innerWidth
  const vh = window.innerHeight

  if (!target || prefer === 'centre') {
    return { left: (vw - card.width) / 2, top: Math.max(MARGIN, (vh - card.height) / 2), arrow: null }
  }

  const midX = target.left + target.width / 2
  const midY = target.top + target.height / 2

  const candidates: { side: Placement; left: number; top: number }[] = [
    { side: 'right', left: target.left + target.width + GAP, top: midY - card.height / 2 },
    { side: 'left', left: target.left - GAP - card.width, top: midY - card.height / 2 },
    { side: 'bottom', left: midX - card.width / 2, top: target.top + target.height + GAP },
    { side: 'top', left: midX - card.width / 2, top: target.top - GAP - card.height },
  ]
  const ordered = [
    ...candidates.filter((c) => c.side === prefer),
    ...candidates.filter((c) => c.side !== prefer),
  ]

  for (const candidate of ordered) {
    // The cross-axis can slide to fit; the main axis cannot, or the card would cover
    // the very thing it is pointing at.
    const left =
      candidate.side === 'right' || candidate.side === 'left'
        ? candidate.left
        : Math.min(Math.max(candidate.left, MARGIN), vw - card.width - MARGIN)
    const top =
      candidate.side === 'top' || candidate.side === 'bottom'
        ? candidate.top
        : Math.min(Math.max(candidate.top, MARGIN), vh - card.height - MARGIN)

    const fits =
      left >= MARGIN &&
      top >= MARGIN &&
      left + card.width <= vw - MARGIN &&
      top + card.height <= vh - MARGIN
    if (fits) return { left, top, arrow: candidate.side }
  }

  return {
    left: Math.min(Math.max(midX - card.width / 2, MARGIN), Math.max(MARGIN, vw - card.width - MARGIN)),
    top: Math.max(MARGIN, vh - card.height - MARGIN),
    arrow: null,
  }
}

export function Tour({
  steps,
  open,
  onClose,
}: {
  steps: TourStep[]
  open: boolean
  onClose: (reason: 'finished' | 'dismissed') => void
}) {
  const [index, setIndex] = useState(0)
  const [rect, setRect] = useState<Rect | null>(null)
  const [card, setCard] = useState({ width: CARD_WIDTH, height: 220 })
  const [narrow, setNarrow] = useState(false)
  const cardRef = useRef<HTMLDivElement | null>(null)
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const restoreFocus = useRef<Element | null>(null)

  const step = steps[index] ?? null
  const last = index === steps.length - 1

  /* Opening always starts at the beginning: a tour resumed halfway through, with the
     app in whatever state the reader left it, explains a screen that is not there. */
  useEffect(() => {
    if (open) setIndex(0)
  }, [open])

  useEffect(() => {
    if (!open) return
    restoreFocus.current = document.activeElement
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
      const target = restoreFocus.current
      if (target instanceof HTMLElement) target.focus()
    }
  }, [open])

  /* Steps that need the app in a particular state — a tab open, a colour scale
     chosen — arrange it on the way in, before anything is measured. */
  useEffect(() => {
    if (!open || !step) return
    step.before?.()
  }, [open, step?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return
    const onResize = () => setNarrow(window.innerWidth < SHEET_BREAKPOINT)
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [open])

  /*
   * Measured every frame rather than on scroll and resize events.
   *
   * The target moves for reasons no event covers: a tab panel re-rendering with taller
   * content, a solver run finishing and pushing the KPI row into existence, a lever
   * expanding. One getBoundingClientRect per frame is far cheaper than being wrong,
   * and state only changes when the rectangle actually does.
   */
  useEffect(() => {
    if (!open || !step) return
    let frame = 0
    const measure = () => {
      const element = step.target ? document.querySelector(step.target) : null
      if (element) {
        const box = element.getBoundingClientRect()
        const padding = step.padding ?? 8
        // Clamped to the viewport: a panel that runs to the edge of the window would
        // otherwise be lit by a rectangle whose outline is half off screen.
        const top = Math.max(0, box.top - padding)
        const left = Math.max(0, box.left - padding)
        const next = {
          top,
          left,
          width: Math.min(box.right + padding, window.innerWidth) - left,
          height: Math.min(box.bottom + padding, window.innerHeight) - top,
        }
        setRect((current) => (sameRect(current, next) ? current : next))
      } else {
        setRect((current) => (current === null ? current : null))
      }
      frame = requestAnimationFrame(measure)
    }
    frame = requestAnimationFrame(measure)
    return () => cancelAnimationFrame(frame)
  }, [open, step?.id, step?.target, step?.padding]) // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    if (!open || !cardRef.current) return
    const box = cardRef.current.getBoundingClientRect()
    setCard((current) =>
      Math.abs(current.width - box.width) < 0.5 && Math.abs(current.height - box.height) < 0.5
        ? current
        : { width: box.width, height: box.height },
    )
  })

  /* Bring the control on screen if the reader has scrolled away from it. */
  useEffect(() => {
    if (!open || !step?.target) return
    const element = document.querySelector(step.target)
    if (!(element instanceof HTMLElement)) return
    const box = element.getBoundingClientRect()
    const hidden = box.bottom < 0 || box.top > window.innerHeight || box.right < 0 || box.left > window.innerWidth
    if (!hidden) return
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    element.scrollIntoView({ block: 'center', inline: 'center', behavior: reduced ? 'auto' : 'smooth' })
  }, [open, step?.id, step?.target])

  useEffect(() => {
    if (!open) return
    // Focus moves to the card's heading on every step so a screen reader reads the new
    // step rather than leaving the reader on a button whose label did not change.
    headingRef.current?.focus()
  }, [open, index])

  const next = useCallback(() => {
    if (last) onClose('finished')
    else setIndex((current) => Math.min(current + 1, steps.length - 1))
  }, [last, onClose, steps.length])

  const back = useCallback(() => setIndex((current) => Math.max(current - 1, 0)), [])

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose('dismissed')
      return
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      next()
      return
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      back()
      return
    }
    if (event.key !== 'Tab' || !cardRef.current) return
    // Nothing outside the card is operable while the tour is open, so Tab cycles
    // within it instead of wandering into a page the reader cannot click.
    const focusable = cardRef.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
    )
    if (!focusable.length) return
    const first = focusable[0]
    const lastEl = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      lastEl.focus()
    } else if (!event.shiftKey && document.activeElement === lastEl) {
      event.preventDefault()
      first.focus()
    }
  }

  if (!open || !step) return null

  const prefer: Placement = narrow ? 'centre' : step.placement ?? 'right'
  const position = narrow
    ? {
        left: MARGIN,
        top: Math.max(MARGIN, window.innerHeight - card.height - MARGIN),
        arrow: null as Placement | null,
      }
    : place(rect, card, prefer)

  return (
    <div className="tour" onKeyDown={onKeyDown}>
      {/* Swallows every click that is not on the card. */}
      <div className="tour-blocker" onClick={() => onClose('dismissed')} />

      {rect && !narrow && (
        <div
          className="tour-ring"
          style={{ top: rect.top, left: rect.left, width: rect.width, height: rect.height }}
          onClick={(event) => event.stopPropagation()}
        />
      )}
      {(!rect || narrow) && <div className="tour-dim" />}

      <div
        ref={cardRef}
        className={`tour-card${narrow ? ' sheet' : ''}`}
        style={
          narrow
            ? { left: MARGIN, top: position.top, width: `calc(100vw - ${MARGIN * 2}px)` }
            : { left: position.left, top: position.top, width: CARD_WIDTH }
        }
        role="dialog"
        aria-modal="true"
        aria-labelledby={`tour-title-${step.id}`}
        aria-describedby={`tour-body-${step.id}`}
      >
        <div className="tour-head">
          <span className="tour-count">
            {index + 1} of {steps.length}
          </span>
          <button
            type="button"
            className="tour-x"
            onClick={() => onClose('dismissed')}
            aria-label="Close the guide"
          >
            ✕
          </button>
        </div>

        <h2 className="tour-title" id={`tour-title-${step.id}`} tabIndex={-1} ref={headingRef}>
          {step.title}
        </h2>
        <div className="tour-body" id={`tour-body-${step.id}`}>
          {step.body}
        </div>

        <div className="tour-progress" aria-hidden="true">
          <span style={{ width: `${((index + 1) / steps.length) * 100}%` }} />
        </div>

        <div className="tour-actions">
          <button type="button" className="btn small ghost" onClick={() => onClose('dismissed')}>
            {last ? 'Close' : 'Skip'}
          </button>
          <div className="tour-actions-right">
            {index > 0 && (
              <button type="button" className="btn small" onClick={back}>
                Back
              </button>
            )}
            <button type="button" className="btn small primary" onClick={next}>
              {last ? 'Start using it' : 'Next'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
