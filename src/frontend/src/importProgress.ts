import { reactive } from 'vue'
import { uploadFile } from './api/client'
import type { ImportProgressState } from './components/ImportProgressModal.vue'

export function useImportProgress() {
  const importState = reactive<ImportProgressState>({
    open: false,
    filename: '',
    phase: 'uploading',
    progress: 0,
    created: 0,
    updated: 0,
    errors: [],
    requestError: '',
  })

  async function runImport(path: string, file: File): Promise<any | null> {
    Object.assign(importState, {
      open: true,
      filename: file.name,
      phase: 'uploading',
      progress: 0,
      created: 0,
      updated: 0,
      errors: [],
      requestError: '',
    })
    try {
      const result = await uploadFile(path, file, (loaded, total) => {
        importState.progress = total ? Math.min(100, Math.round((loaded / total) * 100)) : 0
        if (total && loaded >= total) importState.phase = 'processing'
      })
      Object.assign(importState, {
        phase: 'complete',
        progress: 100,
        created: result.created || 0,
        updated: result.updated || 0,
        errors: result.errors || [],
      })
      return result
    } catch (error: any) {
      importState.phase = 'failed'
      importState.requestError = error?.message || 'The import could not be completed.'
      return null
    }
  }

  function closeImport() {
    if (importState.phase === 'complete' || importState.phase === 'failed') importState.open = false
  }

  return { importState, runImport, closeImport }
}
