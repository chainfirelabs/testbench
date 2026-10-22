/**
 * Test runs: one execution of a pipeline or suite, grouping the tests it
 * recorded and the files it shares with all of them.
 */
import type { FormField } from './components/FormModal.vue'

/** What a person fills in. Start and finish times come from the run itself. */
export const RUN_FORM_FIELDS: FormField[] = [
  { key: 'name', label: 'Name', required: true, placeholder: 'Nightly #1234' },
  {
    key: 'external_id', label: 'External ID',
    hint: 'Optional. Your own ID for the run, such as a CI pipeline ID. Starting a run with an ID that is already in use joins that run.',
  },
  { key: 'url', label: 'Link', placeholder: 'https://ci.example.com/pipelines/1234', hint: 'Optional. The pipeline or job this run came from.' },
  { key: 'notes', label: 'Notes', type: 'textarea' },
]

/** The form's values as the API takes them: blanks are cleared, not sent as "". */
export function runPayload(values: Record<string, any>): Record<string, string | null> {
  const text = (value: any) => {
    const trimmed = String(value ?? '').trim()
    return trimmed || null
  }
  return {
    name: String(values.name ?? '').trim(),
    external_id: text(values.external_id),
    url: text(values.url),
    notes: text(values.notes),
  }
}

/**
 * A run's start or finish time.
 *
 * The API returns UTC without a zone marker (the columns store none), which a
 * browser would otherwise read as local time.
 */
function parseUtc(value: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`)
}

/** A run's start or finish, in the reader's local time. */
export function runTimeLabel(value: string | null | undefined): string {
  if (!value) return ''
  const when = parseUtc(value)
  return Number.isNaN(when.getTime()) ? value : when.toLocaleString()
}

/** How long a run took, once it has finished: "41m 5s", "2h 3m". */
export function runDuration(started: string | null | undefined, finished: string | null | undefined): string {
  if (!started || !finished) return ''
  const ms = parseUtc(finished).getTime() - parseUtc(started).getTime()
  if (!(ms >= 0)) return ''
  const seconds = Math.round(ms / 1000)
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`
}

/** The New Test dialog's keys for its test run and the files the run shares. */
export const TEST_RUN_FIELD = 'test_run_name'
export const TEST_RUN_FILES_FIELD = 'test_run_files'

interface RunChoice { id: string; name: string; started_at?: string | null; created_at?: string | null }

/**
 * The run a typed name means: the newest run of that name, case-insensitively,
 * or null when there is none and the name starts a new one. Runs arrive newest
 * first, so the first match is the newest.
 */
export function matchRun<T extends RunChoice>(name: string | null | undefined, runs: T[]): T | null {
  const wanted = String(name ?? '').trim().toLowerCase()
  if (!wanted) return null
  return runs.find((run) => run.name.trim().toLowerCase() === wanted) ?? null
}

/**
 * The New Test dialog's test run fields: the run, typed with suggestions over
 * the existing ones (a new name starts a run), and files to share with every
 * test in it. On a run's own page the run is fixed.
 */
export function newTestRunFields(
  runs: RunChoice[],
  values: Record<string, any>,
  options: {
    fixedRun?: RunChoice | null
    /** Editing a test: the run it is in now, so clearing the field says what it does. */
    currentName?: string | null
    storageEnabled: boolean
    maxBytes?: number
    noStorage: string
  },
): FormField[] {
  const typed = String(values[TEST_RUN_FIELD] ?? '').trim()
  const match = options.fixedRun ?? matchRun(typed, runs)
  let hint = 'Optional. Pick a test run, or type a new name to start one.'
  if (options.fixedRun) hint = 'Recorded in this test run.'
  else if (!typed && options.currentName) hint = `Blank takes the test out of "${options.currentName}". Its files stay where they are.`
  else if (match) hint = `Joins the run started ${runTimeLabel(match.started_at || match.created_at)}.`
  else if (typed) hint = `A new test run named "${typed}" will be started.`
  const names = [...new Set(runs.map((run) => run.name))]
  const hasRun = (v: Record<string, any>) => !!options.fixedRun || !!String(v[TEST_RUN_FIELD] ?? '').trim()
  return [
    {
      key: TEST_RUN_FIELD, label: 'Test Run', type: 'datalist', disabled: !!options.fixedRun,
      options: names.map((name) => ({ value: name, label: name })), hint,
    },
    {
      key: TEST_RUN_FILES_FIELD, label: 'Test Run Files', type: 'file', multiple: true,
      maxBytes: options.maxBytes,
      disabled: (v) => !options.storageEnabled || !hasRun(v),
      hint: !options.storageEnabled
        ? options.noStorage
        : 'Optional. Files for the whole run, such as a report or test outline, shared with every test in it.',
    },
  ]
}
