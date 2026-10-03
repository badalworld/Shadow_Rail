/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `VITE_HQ_SCANS=on` swaps the procedural cast for licensed Renderpeople GLBs. */
  readonly VITE_HQ_SCANS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
