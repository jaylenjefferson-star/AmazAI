import { useEffect, useRef } from 'react';
import Icon from './Icon';

/**
 * A sheet: rises from the bottom on a phone, floats centred on a desktop.
 *
 * The entrance is a spring (an overshoot curve, not a linear slide) because a
 * sheet that lands softly reads as an object with weight, and one that just
 * appears reads as a page change. It is CSS only, and `prefers-reduced-motion`
 * turns it into a plain fade.
 *
 * Escape and the scrim both close it, focus moves into it and returns to what
 * opened it, and the page behind does not scroll.
 */
export default function Sheet({ title, onClose, children, label, tall = false, back = null }) {
  const ref = useRef(null);

  useEffect(() => {
    const opener = document.activeElement;
    const el = ref.current;
    el?.querySelector('[data-autofocus], input, button, [tabindex]')?.focus?.({ preventScroll: true });
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
      opener?.focus?.({ preventScroll: true });
    };
  }, [onClose]);

  return (
    <div className="sx-layer">
      <div className="sx-scrim" onClick={onClose} aria-hidden="true" />
      <div className={`sx${tall ? ' sx--tall' : ''}`} role="dialog" aria-modal="true"
           aria-label={label || title} ref={ref}>
        <span className="sx-grip" aria-hidden="true" />
        {(title || back) && (
          <header className="sx-head">
            {back
              ? <button type="button" className="sx-round" aria-label="Back" onClick={back}><Icon name="back" size={20} /></button>
              : <span className="sx-round-spacer" />}
            <h2>{title}</h2>
            <button type="button" className="sx-round" aria-label="Close" onClick={onClose}><Icon name="x" size={18} /></button>
          </header>
        )}
        <div className="sx-body">{children}</div>
      </div>
    </div>
  );
}

/** A row in a sheet: icon, a bright title, a muted line, an optional trailing. */
export function SheetRow({ icon, title, hint, onClick, to, danger = false, disabled = false, trailing = null, children = null }) {
  const body = (
    <>
      {icon && <span className="sx-icon"><Icon name={icon} size={20} /></span>}
      {children || (
        <span className="sx-text">
          <strong>{title}</strong>
          {hint && <small>{hint}</small>}
        </span>
      )}
      {trailing}
    </>
  );
  return (
    <button type="button" className={`sx-row${danger ? ' is-danger' : ''}`} disabled={disabled}
            onClick={onClick} data-to={to}>
      {body}
    </button>
  );
}
