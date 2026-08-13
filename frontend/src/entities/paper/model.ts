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
