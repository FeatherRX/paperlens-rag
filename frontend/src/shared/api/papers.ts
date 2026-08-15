import type {
  PaperIngestRequest,
  PaperIngestResponse,
  PaperPrepareResponse,
  PaperSearchResponse,
} from '../../entities/paper/model'
import { requestJson } from './http'

export function searchPapers(query: string): Promise<PaperSearchResponse> {
  const params = new URLSearchParams({ query, limit: '10' })
  return requestJson<PaperSearchResponse>(`/papers/search?${params}`)
}

export function preparePapers(
  paperIds: string[],
): Promise<PaperPrepareResponse> {
  return requestJson<PaperPrepareResponse>('/papers/prepare', {
    method: 'POST',
    body: JSON.stringify({ paper_ids: paperIds }),
  })
}

export function ingestPapers(
  request: PaperIngestRequest,
): Promise<PaperIngestResponse> {
  return requestJson<PaperIngestResponse>('/papers/ingest', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}
