export interface OpenAccessInfo {
  is_oa: boolean | null
  status: string | null
  oa_url: string | null
  any_repository_has_fulltext: boolean | null
}

export interface Paper {
  id: string | null
  title: string | null
  authors: string[]
  institutions: string[]
  publication_year: number | null
  abstract: string | null
  doi: string | null
  landing_page_url: string | null
  open_access: OpenAccessInfo | null
  cited_by_count: number | null
}

export interface PaperSearchResponse {
  query: string
  count: number
  papers: Paper[]
}

export type SourceStatus =
  'fulltext_candidate' | 'abstract_only' | 'unavailable'

export interface OpenAlexContent {
  pdf_available: boolean
  grobid_xml_available: boolean
  content_url: string | null
}

export interface PreparedPaper extends Paper {
  source_status: SourceStatus
  fulltext_url: string | null
  fulltext_license: string | null
  openalex_content: OpenAlexContent
}

export interface PaperPrepareResponse {
  count: number
  papers: PreparedPaper[]
}

export interface PaperIngestRequest {
  paper_ids: string[]
}

export type IngestStatus =
  | 'ingested'
  | 'cached'
  | 'abstract_fallback'
  | 'license_review_required'
  | 'unavailable'
  | 'failed'

export type IngestSourceType = 'grobid_xml' | 'pdf' | 'abstract'

export interface IngestedPaperSummary {
  paper_id: string
  title: string | null
  status: IngestStatus
  source_type: IngestSourceType | null
  license: string | null
  segment_count: number
  character_count: number
  from_cache: boolean
  message: string
}

export interface PaperIngestResponse {
  count: number
  papers: IngestedPaperSummary[]
}

export interface RagAnswerRequest {
  query: string
  paper_ids: string[]
  top_k?: number
}

export interface RagCitation {
  citation_number: number
  paper_id: string
  paper_title: string | null
  chunk_index: number
  page_numbers: number[]
  section_title: string | null
  evidence_excerpt: string
  retrieval_score: number
}

export interface RagAnswerResponse {
  answer: string
  citations: RagCitation[]
}
