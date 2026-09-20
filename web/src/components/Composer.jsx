import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import Companion from '../characters/Companion';
import Icon from './Icon';

/**
 * The composer.
 *
 * One pill: `+` on the left, the text, and on the right either the microphone,
 * the send arrow, or -- while a run is going -- **Stop now**. That last control
 * is the point of the design: when a Bot is working, the most useful thing the
 * box can do is let you stop it or change its mind, and it should say so in
 * words rather than hide a stop icon in a corner.
 *
 * What is real here and what is not:
 *
 *   `/`  skills this Bot actually has (assigned and active), plus a few
 *        commands the console itself runs. A skill token is sent as text and the
 *        API checks it against the Bot's assignments; an unknown `/word` is just
 *        text, never an error.
 *   `@`  Bots you can address. In a channel that wakes exactly the ones named; in
 *        a direct thread it asks this Bot to hand the work over.
 *   `+`  attaches **text** files, inlined into the message. Photos and PDFs need
 *        upload storage that does not exist yet, and the menu says so instead of
 *        offering a button that fails.
 *   mic  browser dictation (the Web Speech API). Where the browser has none, the
 *        button says so.
 *   send while a run is going = redirect: the current run is stopped and this
 *        message is what it does next.
 */

const TEXT_TYPES = /\.(txt|md|markdown|csv|tsv|json|ya?ml|log|js|jsx|ts|tsx|py|rb|go|rs|java|c|h|cpp|sh|sql|html|css|xml|toml|ini|env\.example)$/i;
const MAX_FILE = 64 * 1024;
const MAX_TOTAL = 128 * 1024;
const MAX_FILES = 3;

const kb = (n) => `${Math.max(1, Math.round(n / 1024))} KB`;

/** The token being typed at the caret, if it is one a menu applies to. */
function tokenAt(text, caret) {
  const before = text.slice(0, caret);
  const slash = /^\/([\w-]*)$/.exec(before);
  if (slash) return { kind: 'slash', query: slash[1].toLowerCase(), start: 0, end: caret };
  const mention = /(^|\s)@([\w-]*)$/.exec(before);
  if (mention) {
    const query = mention[2];
    return { kind: 'mention', query: query.toLowerCase(), start: caret - query.length - 1, end: caret };
  }
  return null;
}

export default function Composer({
  name = 'this Bot', mentionables = [], skills = [], commands = [],
  busy = false, canRedirect = true, disabled = false, onSend, onStop, onCommand,
}) {
  const [text, setText] = useState('');
  const [caret, setCaret] = useState(0);
  const [menuIndex, setMenuIndex] = useState(0);
  const [menuShut, setMenuShut] = useState(false);
  const [plusOpen, setPlusOpen] = useState(false);
  const [files, setFiles] = useState([]);
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [voice, setVoice] = useState('idle');   // idle | listening | unsupported | denied
  const box = useRef(null);
  const fileInput = useRef(null);
  const recognition = useRef(null);
  const voiceBase = useRef('');

  // --- the menu under the caret ---------------------------------------------

  const token = useMemo(() => (menuShut ? null : tokenAt(text, caret)), [text, caret, menuShut]);

  const items = useMemo(() => {
    if (!token) return [];
    if (token.kind === 'mention') {
      return mentionables
        .filter((m) => `${m.id} ${m.name}`.toLowerCase().includes(token.query))
        .map((m) => ({ key: m.id, label: m.name, hint: `@${m.id}`, insert: `@${m.id} `, mark: m }));
    }
    const skill = skills
      .filter((s) => `${s.id} ${s.name}`.toLowerCase().includes(token.query))
      .map((s) => ({ key: `skill:${s.id}`, group: 'Skills', label: s.name, hint: s.description,
                     insert: `/${s.id} ` }));
    const cmd = commands
      .filter((c) => c.key.includes(token.query))
      .map((c) => ({ key: `cmd:${c.key}`, group: 'Commands', label: `/${c.key}`, hint: c.hint,
                     insert: `/${c.key} ` }));
    return [...skill, ...cmd];
  }, [token, mentionables, skills, commands]);

  useEffect(() => { setMenuIndex(0); }, [token?.kind, token?.query]);
  const menuOpen = Boolean(token) && items.length > 0;

  // --- sizing -----------------------------------------------------------------

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
  }, [text]);

  // --- voice ------------------------------------------------------------------

  const Speech = typeof window !== 'undefined'
    ? (window.SpeechRecognition || window.webkitSpeechRecognition) : null;

  useEffect(() => () => recognition.current?.abort?.(), []);

  function toggleVoice() {
    if (!Speech) { setVoice('unsupported'); return; }
    if (voice === 'listening') { recognition.current?.stop(); return; }
    const rec = new Speech();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = navigator.language || 'en-US';
    voiceBase.current = text;
    rec.onresult = (e) => {
      let heard = '';
      for (let i = 0; i < e.results.length; i += 1) heard += e.results[i][0].transcript;
      const base = voiceBase.current;
      setText(`${base}${base && !/\s$/.test(base) ? ' ' : ''}${heard}`);
    };
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') setVoice('denied');
      else if (e.error !== 'no-speech' && e.error !== 'aborted') { setVoice('idle'); setError(`Voice input stopped: ${e.error}`); }
    };
    rec.onend = () => setVoice((v) => (v === 'denied' ? v : 'idle'));
    recognition.current = rec;
    try { rec.start(); setVoice('listening'); setError(''); } catch { setVoice('idle'); }
  }

  // --- attachments --------------------------------------------------------------

  async function addFiles(list) {
    setError('');
    const next = [...files];
    for (const file of Array.from(list)) {
      if (next.length >= MAX_FILES) { setError(`Attach up to ${MAX_FILES} files at a time.`); break; }
      const looksText = file.type.startsWith('text/') || file.type === 'application/json' || TEXT_TYPES.test(file.name);
      if (!looksText) {
        setError('Only text files can be attached right now. Photos and PDFs need upload storage that is not set up yet.');
        continue;
      }
      if (file.size > MAX_FILE) { setError(`${file.name} is over ${kb(MAX_FILE)}; attach a smaller excerpt.`); continue; }
      const body = await file.text();
      if (next.reduce((n, f) => n + f.size, 0) + file.size > MAX_TOTAL) { setError(`Attachments are limited to ${kb(MAX_TOTAL)} together.`); break; }
      next.push({ name: file.name, size: file.size, body });
    }
    setFiles(next);
  }

  // --- sending --------------------------------------------------------------------

  const compose = useCallback(() => {
    const parts = [text.trim()];
    for (const f of files) parts.push(`---\nAttached: ${f.name} (${kb(f.size)})\n\`\`\`\n${f.body}\n\`\`\``);
    return parts.filter(Boolean).join('\n\n');
  }, [text, files]);

  async function submit() {
    const message = compose();
    if (!message || sending || disabled) return;
    recognition.current?.stop?.();

    // A console command, not a message: `/remember …` saves to memory, and so on.
    const command = /^\/([\w-]+)(?:\s+([\s\S]*))?$/.exec(text.trim());
    const known = command && commands.find((c) => c.key === command[1].toLowerCase());
    setSending(true);
    setError('');
    try {
      if (known) await onCommand?.(known.key, (command[2] || '').trim());
      else await onSend(message, { redirect: busy && canRedirect });
      setText(''); setFiles([]); setMenuShut(false);
    } catch (err) {
      setError(err.message || 'That did not send.');
    } finally {
      setSending(false);
    }
  }

  function pick(item) {
    const start = token.start;
    const next = `${text.slice(0, start)}${item.insert}${text.slice(token.end)}`;
    setText(next);
    const at = start + item.insert.length;
    requestAnimationFrame(() => {
      box.current?.focus();
      box.current?.setSelectionRange(at, at);
      setCaret(at);
    });
  }

  function onKeyDown(e) {
    if (menuOpen) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMenuIndex((i) => (i + 1) % items.length); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setMenuIndex((i) => (i - 1 + items.length) % items.length); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(items[menuIndex]); return; }
      if (e.key === 'Escape') { e.preventDefault(); setMenuShut(true); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  const hasText = Boolean(text.trim() || files.length);
  // "Stop now" is drawn right beside it, so the placeholder need not say so too.
  const placeholder = busy && canRedirect ? `Redirect ${name}…` : `Message ${name}`;
  const grouped = items.reduce((acc, it, i) => {
    const g = it.group || '';
    if (!acc.length || acc[acc.length - 1].group !== g) acc.push({ group: g, rows: [] });
    acc[acc.length - 1].rows.push({ ...it, i });
    return acc;
  }, []);

  return (
    <form className="cmp" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      {files.length > 0 && (
        <ul className="cmp-files" aria-label="Attachments">
          {files.map((f) => (
            <li key={f.name}>
              <Icon name="file" size={15} />
              <span>{f.name}</span><small>{kb(f.size)}</small>
              <button type="button" aria-label={`Remove ${f.name}`}
                      onClick={() => setFiles((fs) => fs.filter((x) => x.name !== f.name))}>
                <Icon name="x" size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {menuOpen && (
        <div className="cmp-menu" role="listbox" aria-label={token.kind === 'mention' ? 'Mention a Bot' : 'Skills and commands'}>
          {grouped.map((g) => (
            <div key={g.group || 'mentions'}>
              {g.group && <div className="cmp-group">{g.group}</div>}
              {g.rows.map((it) => (
                <button key={it.key} type="button" role="option" id={`cmp-opt-${it.i}`}
                        aria-selected={it.i === menuIndex}
                        className={`cmp-opt${it.i === menuIndex ? ' is-on' : ''}`}
                        onMouseEnter={() => setMenuIndex(it.i)}
                        onMouseDown={(e) => { e.preventDefault(); pick(it); }}>
                  {it.mark && <Companion archetype={it.mark.archetype} color={it.mark.color} state="idle" size={24} name={it.mark.name} />}
                  <span className="cmp-opt-text"><strong>{it.label}</strong><small>{it.hint}</small></span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}

      {plusOpen && (
        <>
          <div className="cmp-scrim" onClick={() => setPlusOpen(false)} />
          <div className="cmp-plus" role="menu">
            <button type="button" role="menuitem"
                    onClick={() => { setPlusOpen(false); fileInput.current?.click(); }}>
              <Icon name="paperclip" size={18} />
              <span><strong>Attach a text file</strong><small>Notes, CSV, JSON, code — up to {kb(MAX_FILE)} each</small></span>
            </button>
            <button type="button" role="menuitem" disabled>
              <Icon name="file" size={18} />
              <span><strong>Photo or PDF</strong><small>Needs upload storage, which is not set up yet</small></span>
            </button>
            <button type="button" role="menuitem"
                    onClick={() => { setPlusOpen(false); setText((t) => `${t}${t && !/\s$/.test(t) ? ' ' : ''}/`); setMenuShut(false); box.current?.focus(); }}>
              <Icon name="layers" size={18} />
              <span><strong>Use a skill</strong><small>Type / for skills and commands</small></span>
            </button>
            {mentionables.length > 0 && (
              <button type="button" role="menuitem"
                      onClick={() => { setPlusOpen(false); setText((t) => `${t}${t && !/\s$/.test(t) ? ' ' : ''}@`); setMenuShut(false); box.current?.focus(); }}>
                <Icon name="inbox" size={18} />
                <span><strong>Mention a Bot</strong><small>Type @ to address one directly</small></span>
              </button>
            )}
          </div>
        </>
      )}

      <input ref={fileInput} type="file" multiple hidden
             onChange={(e) => { addFiles(e.target.files); e.target.value = ''; }} />

      {(error || voice === 'unsupported' || voice === 'denied' || (busy && canRedirect && hasText)) && (
        <p className={`cmp-note${error || voice === 'denied' ? ' is-error' : ''}`} role="status">
          {error
            || (voice === 'unsupported' && 'Voice input is not available in this browser.')
            || (voice === 'denied' && 'Microphone access was blocked. Allow it in the browser to dictate.')
            || `Sending now stops ${name}'s current work and continues with your message.`}
        </p>
      )}

      <div className={`cmp-box${busy ? ' is-busy' : ''}${voice === 'listening' ? ' is-listening' : ''}`}>
        <button type="button" className="cmp-btn" aria-label="Attach or add" aria-haspopup="menu"
                aria-expanded={plusOpen} onClick={() => setPlusOpen((o) => !o)}>
          <Icon name="plus" size={20} />
        </button>

        <textarea ref={box} rows={1} value={text} disabled={disabled}
                  placeholder={voice === 'listening' ? 'Listening…' : placeholder}
                  aria-label={placeholder} aria-autocomplete="list"
                  aria-expanded={menuOpen}
                  aria-activedescendant={menuOpen ? `cmp-opt-${menuIndex}` : undefined}
                  onChange={(e) => { setText(e.target.value); setCaret(e.target.selectionStart); setMenuShut(false); }}
                  onSelect={(e) => setCaret(e.target.selectionStart)}
                  onKeyDown={onKeyDown} />

        {busy && (
          <button type="button" className="cmp-stop" onClick={onStop}>
            <Icon name="stop" size={15} />Stop now
          </button>
        )}

        {hasText ? (
          <button type="submit" className="cmp-send" disabled={sending || disabled}
                  aria-label={busy && canRedirect ? 'Redirect with this message' : 'Send'}>
            <Icon name="arrowUp" size={18} />
          </button>
        ) : (
          <button type="button" className={`cmp-send cmp-mic${voice === 'listening' ? ' is-on' : ''}`}
                  aria-label={voice === 'listening' ? 'Stop dictation' : 'Dictate'}
                  aria-pressed={voice === 'listening'}
                  disabled={voice === 'unsupported'}
                  title={voice === 'unsupported' ? 'Voice input is not available in this browser' : undefined}
                  onClick={toggleVoice}>
            <Icon name="mic" size={18} />
          </button>
        )}
      </div>
      <span className="sr-only" aria-live="polite">{voice === 'listening' ? 'Listening' : ''}</span>
    </form>
  );
}
