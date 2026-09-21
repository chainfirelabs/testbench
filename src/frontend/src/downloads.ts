import { ref } from 'vue'
import { downloadFile } from './api/client'

/**
 * Exports and templates, with the two things a bare `downloadFile` call lacks.
 *
 * `downloadFile` reads the whole response before the browser is handed
 * anything, because the request needs an Authorization header and so cannot be
 * a plain link. That is fine, but it means a large export sits silently for as
 * long as the server takes to build it — and every call site used to start it
 * without awaiting it, so a rejection became an unhandled promise and the user
 * saw nothing at all. A failed export and a slow one looked identical: a button
 * that did nothing.
 *
 * So: `downloading` for the button to show it is working, and a caught error
 * routed to the page's own toast. Concurrent clicks are dropped rather than
 * queued — the second one would produce a second copy of the same file.
 */
export function useDownload(notify: (message: string, isError?: boolean) => void) {
  const downloading = ref(false)

  async function download(path: string, filename: string, label = 'download'): Promise<void> {
    if (downloading.value) return
    downloading.value = true
    try {
      await downloadFile(path, filename)
    } catch (error: any) {
      notify(error?.message || `The ${label} could not be completed.`, true)
    } finally {
      downloading.value = false
    }
  }

  return { downloading, download }
}
