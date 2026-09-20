/**
 * Schedules a person (or a Bot's proposal) can choose, and how to say them.
 *
 * Six presets, not a free field. The console does not know what
 * `routines.CRON_RE` / `RATE_RE` will accept on the day someone types a
 * cron string, and a form that lets a Bot's *proposal* carry an arbitrary
 * expression would let a model author a schedule the operator never read.
 * These six are known-good, which is also why a routine proposal can only name
 * one of them by key.
 */
export const SCHEDULE_PRESETS = [
  { key: 'every-30-min', label: 'Every 30 minutes', expression: 'rate(30 minutes)' },
  { key: 'hourly', label: 'Every hour', expression: 'rate(1 hour)' },
  { key: 'weekday-9', label: 'Weekdays at 9:00 AM', expression: 'cron(0 9 ? * MON-FRI *)' },
  { key: 'daily-730', label: 'Every day at 7:30 AM', expression: 'cron(30 7 * * ? *)' },
  { key: 'nightly-2', label: 'Every night at 2:00 AM', expression: 'cron(0 2 * * ? *)' },
  { key: 'custom', label: 'Custom expression…', expression: '' },
];

const clock = (hour, minute) => new Date(2000, 0, 1, Number(hour), Number(minute))
  .toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

/** An EventBridge expression, in words where it is a shape we recognise and as
 *  written where it is not -- a wrong paraphrase of a schedule is worse than
 *  the raw string. */
function humanize(expression) {
  const preset = SCHEDULE_PRESETS.find((p) => p.expression && p.expression === expression);
  if (preset) return preset.label;

  const cron = /^cron\((\d+) (\d+) (\S+) (\S+) (\S+) (\S+)\)$/.exec(expression);
  if (cron) {
    const [, minute, hour, dom, month, dow] = cron;
    const anyDay = (v) => v === '*' || v === '?';
    if (month === '*' && dow === 'MON-FRI' && anyDay(dom)) return `Weekdays at ${clock(hour, minute)}`;
    if (month === '*' && anyDay(dom) && anyDay(dow)) return `Every day at ${clock(hour, minute)}`;
  }

  const rate = /^rate\((\d+) (minute|minutes|hour|hours|day|days)\)$/.exec(expression);
  if (rate) {
    const unit = rate[2].replace(/s$/, '');
    return rate[1] === '1' ? `Every ${unit}` : `Every ${rate[1]} ${unit}s`;
  }
  return expression;
}

/** One line for a routine's trigger. */
export function describeSchedule(trigger) {
  if (!trigger || trigger.type === 'manual') return 'Runs only when you start it';
  if (trigger.type === 'webhook') return 'Runs when triggered externally';
  return trigger.expression ? humanize(trigger.expression) : 'Scheduled';
}
