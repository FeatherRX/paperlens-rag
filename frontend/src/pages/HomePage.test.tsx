import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type {
  IngestedPaperSummary,
  IngestStatus,
  Paper,
  PaperIngestResponse,
  PaperPrepareResponse,
  PaperSearchResponse,
  RagAnswerResponse,
  SourceStatus,
} from '../entities/paper/model'
import { HomePage } from './HomePage'

function createPaper(index: number): Paper {
  return {
    id: `https://openalex.org/W${index}`,
    title: `Test Paper ${index}`,
    authors: [`Author ${index}`],
    institutions: [`Institute ${index}`],
    publication_year: 2020 + index,
    abstract: `Original abstract ${index}`,
    doi: `https://doi.org/10.1000/${index}`,
    landing_page_url: `https://example.org/papers/${index}`,
    open_access: {
      is_oa: index % 2 === 1,
      status: index % 2 === 1 ? 'gold' : 'closed',
      oa_url: index % 2 === 1 ? `https://example.org/oa/${index}` : null,
      any_repository_has_fulltext: index % 2 === 1,
    },
    cited_by_count: index * 10,
  }
}

const papers = Array.from({ length: 6 }, (_, index) => createPaper(index + 1))

function searchResponse(items: Paper[] = papers): PaperSearchResponse {
  return {
    query: 'retrieval augmented generation',
    count: items.length,
    papers: items,
  }
}

function preparedPaper(paper: Paper, sourceStatus: SourceStatus) {
  return {
    ...paper,
    source_status: sourceStatus,
    fulltext_url:
      sourceStatus === 'fulltext_candidate'
        ? `https://example.org/fulltext/${paper.id?.split('/').at(-1)}.pdf`
        : null,
    fulltext_license: sourceStatus === 'fulltext_candidate' ? 'cc-by' : null,
    openalex_content: {
      pdf_available: sourceStatus === 'fulltext_candidate',
      grobid_xml_available: false,
      content_url: null,
    },
  }
}

function ingestedPaper(
  paper: Paper,
  status: IngestStatus,
  overrides: Partial<IngestedPaperSummary> = {},
): IngestedPaperSummary {
  const sourceType =
    status === 'ingested'
      ? 'grobid_xml'
      : status === 'cached'
        ? 'pdf'
        : status === 'abstract_fallback'
          ? 'abstract'
          : null
  return {
    paper_id: paper.id?.split('/').at(-1) ?? '',
    title: paper.title,
    status,
    source_type: sourceType,
    license: sourceType && sourceType !== 'abstract' ? 'cc-by' : null,
    segment_count: sourceType ? 2 : 0,
    character_count: sourceType ? 240 : 0,
    from_cache: status === 'cached',
    message: `Result: ${status}`,
    ...overrides,
  }
}

function successfulIngestionResponse(
  selectedIndexes = [2, 0, 1],
): PaperIngestResponse {
  return {
    count: selectedIndexes.length,
    papers: selectedIndexes.map((index) =>
      ingestedPaper(papers[index], 'ingested'),
    ),
  }
}

function jsonResponse(data: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(data), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

function renderHome() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <HomePage />
    </QueryClientProvider>,
  )
}

async function runSearch(fetchMock: ReturnType<typeof vi.fn>) {
  fetchMock.mockImplementationOnce(() => jsonResponse(searchResponse()))
  const user = userEvent.setup()
  await user.type(
    screen.getByRole('searchbox', { name: '研究主题' }),
    'retrieval augmented generation',
  )
  await user.click(screen.getByRole('button', { name: '搜索论文' }))
  await screen.findByText('Test Paper 1')
  return user
}

async function prepareSelection(
  fetchMock: ReturnType<typeof vi.fn>,
  user: ReturnType<typeof userEvent.setup>,
  selectedIndexes = [2, 0, 1],
) {
  const response: PaperPrepareResponse = {
    count: selectedIndexes.length,
    papers: selectedIndexes.map((index) =>
      preparedPaper(papers[index], 'fulltext_candidate'),
    ),
  }
  fetchMock.mockImplementationOnce(() => jsonResponse(response))
  for (const index of selectedIndexes) {
    await user.click(
      screen.getByRole('checkbox', {
        name: `选择论文：Test Paper ${index + 1}`,
      }),
    )
  }
  await user.click(screen.getByRole('button', { name: '准备分析' }))
  await screen.findByText(`已检查 ${selectedIndexes.length} 篇论文`)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('HomePage', () => {
  it('renders the research-topic entry point without selecting papers', () => {
    renderHome()

    expect(
      screen.getByRole('heading', {
        name: '从研究主题开始，找到值得深入阅读的论文',
      }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('searchbox', { name: '研究主题' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('shows the loading state while a search is in progress', async () => {
    let resolveSearch!: (value: Response) => void
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveSearch = resolve
        }),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = userEvent.setup()

    await user.type(
      screen.getByRole('searchbox', { name: '研究主题' }),
      'graph neural networks',
    )
    await user.click(screen.getByRole('button', { name: '搜索论文' }))

    expect(screen.getByText('正在检索相关论文…')).toBeInTheDocument()
    resolveSearch(
      new Response(JSON.stringify(searchResponse([])), {
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await screen.findByText('没有找到相关论文，请尝试调整研究主题。')
  })

  it('sends a trimmed topic with limit 10 and renders results', async () => {
    const fetchMock = vi.fn(() => jsonResponse(searchResponse()))
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = userEvent.setup()

    await user.type(
      screen.getByRole('searchbox', { name: '研究主题' }),
      '  retrieval augmented generation  ',
    )
    await user.click(screen.getByRole('button', { name: '搜索论文' }))

    expect(await screen.findByText('Test Paper 1')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/papers/search?query=retrieval+augmented+generation&limit=10',
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: 'application/json' }),
      }),
    )
    expect(screen.getAllByText('论文原始摘要')).toHaveLength(6)
    expect(screen.getByText('Original abstract 1')).toBeInTheDocument()
    expect(screen.getByText('Author 1')).toBeInTheDocument()
    expect(screen.getByText('Institute 1')).toBeInTheDocument()
  })

  it('shows an empty result and a search error without crashing', async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => jsonResponse(searchResponse([])))
      .mockImplementationOnce(() =>
        jsonResponse({ detail: 'OpenAlex 暂时不可用' }, 502),
      )
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = userEvent.setup()
    const searchbox = screen.getByRole('searchbox', { name: '研究主题' })

    await user.type(searchbox, 'first topic')
    await user.click(screen.getByRole('button', { name: '搜索论文' }))
    expect(
      await screen.findByText('没有找到相关论文，请尝试调整研究主题。'),
    ).toBeInTheDocument()

    await user.clear(searchbox)
    await user.type(searchbox, 'second topic')
    await user.click(screen.getByRole('button', { name: '搜索论文' }))
    expect(
      await screen.findByText('论文搜索失败：OpenAlex 暂时不可用'),
    ).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: '研究主题' })).toBeEnabled()
  })

  it('requires three selections and prevents selecting a sixth paper', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    const prepareButton = screen.getByRole('button', { name: '准备分析' })
    const checkboxes = screen.getAllByRole('checkbox')

    expect(checkboxes).toHaveLength(6)
    for (const checkbox of checkboxes) expect(checkbox).not.toBeChecked()
    expect(prepareButton).toBeDisabled()

    await user.click(checkboxes[0])
    await user.click(checkboxes[1])
    expect(prepareButton).toBeDisabled()
    await user.click(checkboxes[2])
    expect(prepareButton).toBeEnabled()

    await user.click(checkboxes[3])
    await user.click(checkboxes[4])
    expect(screen.getByText('已选择 5/5')).toBeInTheDocument()
    expect(checkboxes[5]).toBeDisabled()
    await user.click(checkboxes[5])
    expect(checkboxes[5]).not.toBeChecked()
  })

  it('prepares papers in selection order and explains all source statuses', async () => {
    const prepareResponse: PaperPrepareResponse = {
      count: 3,
      papers: [
        preparedPaper(papers[2], 'fulltext_candidate'),
        preparedPaper(papers[0], 'abstract_only'),
        preparedPaper(papers[1], 'unavailable'),
      ],
    }
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    fetchMock.mockImplementationOnce(() => jsonResponse(prepareResponse))

    await user.click(
      screen.getByRole('checkbox', { name: '选择论文：Test Paper 3' }),
    )
    await user.click(
      screen.getByRole('checkbox', { name: '选择论文：Test Paper 1' }),
    )
    await user.click(
      screen.getByRole('checkbox', { name: '选择论文：Test Paper 2' }),
    )
    await user.click(screen.getByRole('button', { name: '准备分析' }))

    expect(await screen.findByText('已检查 3 篇论文')).toBeInTheDocument()
    const [, prepareCall] = fetchMock.mock.calls
    expect(prepareCall[0]).toBe('/api/papers/prepare')
    expect(JSON.parse(prepareCall[1].body)).toEqual({
      paper_ids: [
        'https://openalex.org/W3',
        'https://openalex.org/W1',
        'https://openalex.org/W2',
      ],
    })
    expect(screen.getByText('开放全文候选')).toBeInTheDocument()
    expect(
      screen.getByText('尚未下载，且尚未完成许可证复核。'),
    ).toBeInTheDocument()
    expect(screen.getByText('仅原始摘要')).toBeInTheDocument()
    expect(screen.getByText('当前仅可使用论文原始摘要。')).toBeInTheDocument()
    expect(screen.getByText('语料不可用')).toBeInTheDocument()
    expect(screen.getByText('暂时没有可分析语料。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '确认摄取' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('shows a prepare error and keeps the page usable', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    fetchMock.mockImplementationOnce(() =>
      jsonResponse({ detail: '来源准备服务暂时不可用' }, 502),
    )

    await user.click(
      screen.getByRole('checkbox', { name: '选择论文：Test Paper 1' }),
    )
    await user.click(
      screen.getByRole('checkbox', { name: '选择论文：Test Paper 2' }),
    )
    await user.click(
      screen.getByRole('checkbox', { name: '选择论文：Test Paper 3' }),
    )
    await user.click(screen.getByRole('button', { name: '准备分析' }))

    expect(
      await screen.findByText('来源准备失败：来源准备服务暂时不可用'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '准备分析' })).toBeEnabled()
  })

  it('submits selected IDs once and renders successful ingestion statuses', async () => {
    const response: PaperIngestResponse = {
      count: 3,
      papers: [
        ingestedPaper(papers[2], 'ingested'),
        ingestedPaper(papers[0], 'cached'),
        ingestedPaper(papers[1], 'abstract_fallback'),
      ],
    }
    let resolveIngestion!: (value: Response) => void
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    await prepareSelection(fetchMock, user)
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveIngestion = resolve
        }),
    )

    const ingestButton = screen.getByRole('button', { name: '确认摄取' })
    await user.click(ingestButton)

    expect(ingestButton).toBeDisabled()
    expect(ingestButton).toHaveTextContent('摄取中…')
    expect(screen.getByText('已检查 3 篇论文')).toBeInTheDocument()
    await user.click(ingestButton)
    expect(fetchMock).toHaveBeenCalledTimes(3)

    const ingestCall = fetchMock.mock.calls[2]
    expect(ingestCall[0]).toBe('/api/papers/ingest')
    expect(JSON.parse(ingestCall[1].body)).toEqual({
      paper_ids: [
        'https://openalex.org/W3',
        'https://openalex.org/W1',
        'https://openalex.org/W2',
      ],
    })

    resolveIngestion(
      new Response(JSON.stringify(response), {
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    expect(await screen.findByText('已处理 3 篇论文')).toBeInTheDocument()
    expect(screen.getByText('已摄取')).toBeInTheDocument()
    expect(screen.getByText('已使用缓存')).toBeInTheDocument()
    expect(screen.getByText('已回退原始摘要')).toBeInTheDocument()
    expect(screen.getByText('GROBID XML')).toBeInTheDocument()
    expect(screen.getByText('PDF')).toBeInTheDocument()
    expect(screen.getByText('原始摘要')).toBeInTheDocument()
    expect(screen.getByText('是')).toBeInTheDocument()
  })

  it('renders mixed per-paper failures as results instead of a request error', async () => {
    const response: PaperIngestResponse = {
      count: 3,
      papers: [
        ingestedPaper(papers[2], 'license_review_required'),
        ingestedPaper(papers[0], 'unavailable'),
        ingestedPaper(papers[1], 'failed'),
      ],
    }
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    await prepareSelection(fetchMock, user)
    fetchMock.mockImplementationOnce(() => jsonResponse(response))

    await user.click(screen.getByRole('button', { name: '确认摄取' }))

    expect(await screen.findByText('需要许可证复核')).toBeInTheDocument()
    expect(screen.getByText('语料不可用')).toBeInTheDocument()
    expect(screen.getByText('处理失败')).toBeInTheDocument()
    expect(screen.getByText('Result: failed')).toBeInTheDocument()
    expect(screen.queryByText(/语料摄取请求失败/)).not.toBeInTheDocument()
    expect(screen.getByText('0 篇可用于问答')).toBeInTheDocument()
    expect(
      screen.getByText(/当前只有 0 篇论文形成了可检索本地文档/),
    ).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '研究问题' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '提问' })).toBeDisabled()
  })

  it('filters mixed ingestion results and preserves selection order for RAG', async () => {
    const selectedIndexes = [4, 1, 3, 0, 2]
    const response: PaperIngestResponse = {
      count: 5,
      papers: [
        ingestedPaper(papers[0], 'failed'),
        ingestedPaper(papers[3], 'cached'),
        ingestedPaper(papers[4], 'ingested'),
        ingestedPaper(papers[2], 'abstract_fallback'),
        ingestedPaper(papers[1], 'unavailable'),
      ],
    }
    const ragResponse: RagAnswerResponse = {
      answer: 'Answer grounded in the filtered corpus [1].',
      citations: [],
    }
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    await prepareSelection(fetchMock, user, selectedIndexes)
    fetchMock.mockImplementationOnce(() => jsonResponse(response))

    await user.click(screen.getByRole('button', { name: '确认摄取' }))

    expect(await screen.findByText('已处理 5 篇论文')).toBeInTheDocument()
    expect(screen.getByText('3 篇可用于问答')).toBeInTheDocument()
    fetchMock.mockImplementationOnce(() => jsonResponse(ragResponse))
    await user.type(
      screen.getByRole('textbox', { name: '研究问题' }),
      'What does the available evidence show?',
    )
    await user.click(screen.getByRole('button', { name: '提问' }))

    await screen.findByText('Answer grounded in the filtered corpus [1].')
    const ragCall = fetchMock.mock.calls[3]
    expect(ragCall[0]).toBe('/api/rag/answer')
    expect(JSON.parse(ragCall[1].body)).toEqual({
      query: 'What does the available evidence show?',
      paper_ids: [
        'https://openalex.org/W5',
        'https://openalex.org/W4',
        'https://openalex.org/W3',
      ],
    })
  })

  it('blocks RAG when fewer than three ingested papers are searchable', async () => {
    const response: PaperIngestResponse = {
      count: 3,
      papers: [
        ingestedPaper(papers[2], 'ingested'),
        ingestedPaper(papers[0], 'license_review_required'),
        ingestedPaper(papers[1], 'cached'),
      ],
    }
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    await prepareSelection(fetchMock, user)
    fetchMock.mockImplementationOnce(() => jsonResponse(response))

    await user.click(screen.getByRole('button', { name: '确认摄取' }))

    expect(await screen.findByText('已处理 3 篇论文')).toBeInTheDocument()
    expect(screen.getByText('2 篇可用于问答')).toBeInTheDocument()
    expect(screen.getByText(/至少需要 3 篇才能发起问答/)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '研究问题' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '提问' })).toBeDisabled()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('shows a request-level ingestion error without hiding prepare results', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    await prepareSelection(fetchMock, user)
    fetchMock.mockImplementationOnce(() =>
      jsonResponse({ detail: '摄取服务暂时不可用' }, 502),
    )

    await user.click(screen.getByRole('button', { name: '确认摄取' }))

    expect(
      await screen.findByText('语料摄取请求失败：摄取服务暂时不可用'),
    ).toBeInTheDocument()
    expect(screen.getByText('已检查 3 篇论文')).toBeInTheDocument()
    expect(screen.queryByText(/已处理/)).not.toBeInTheDocument()
  })

  it('asks against the ingested selection once and renders linked citations', async () => {
    const ragResponse: RagAnswerResponse = {
      answer:
        '模型不确定且需要外部知识时应触发检索 [1]，并依据后续生成意图形成查询 [2]。',
      citations: [
        {
          citation_number: 1,
          paper_id: 'W3',
          paper_title: 'Test Paper 3',
          chunk_index: 4,
          page_numbers: [3],
          section_title: 'Methods',
          evidence_excerpt: 'Uncertainty indicates that retrieval is needed.',
          retrieval_score: 0.873576,
        },
        {
          citation_number: 2,
          paper_id: 'W1',
          paper_title: 'Test Paper 1',
          chunk_index: 2,
          page_numbers: [],
          section_title: null,
          evidence_excerpt: 'Future generation intent guides the query.',
          retrieval_score: 0.84525,
        },
      ],
    }
    let resolveAnswer!: (value: Response) => void
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    await prepareSelection(fetchMock, user)
    expect(
      screen.queryByRole('textbox', { name: '研究问题' }),
    ).not.toBeInTheDocument()
    fetchMock.mockImplementationOnce(() =>
      jsonResponse(successfulIngestionResponse()),
    )
    await user.click(screen.getByRole('button', { name: '确认摄取' }))

    expect(await screen.findByText('已处理 3 篇论文')).toBeInTheDocument()
    const queryInput = screen.getByRole('textbox', { name: '研究问题' })
    const askButton = screen.getByRole('button', { name: '提问' })
    expect(askButton).toBeDisabled()
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveAnswer = resolve
        }),
    )

    await user.type(
      queryInput,
      '  When should active retrieval use external information?  ',
    )
    await user.click(askButton)

    expect(askButton).toBeDisabled()
    expect(askButton).toHaveTextContent('生成回答中…')
    expect(
      screen.getByText('正在检索证据并生成回答，请稍候…'),
    ).toBeInTheDocument()
    expect(screen.getByText('已检查 3 篇论文')).toBeInTheDocument()
    expect(screen.getByText('已处理 3 篇论文')).toBeInTheDocument()
    await user.click(askButton)
    expect(fetchMock).toHaveBeenCalledTimes(4)

    const ragCall = fetchMock.mock.calls[3]
    expect(ragCall[0]).toBe('/api/rag/answer')
    expect(JSON.parse(ragCall[1].body)).toEqual({
      query: 'When should active retrieval use external information?',
      paper_ids: [
        'https://openalex.org/W3',
        'https://openalex.org/W1',
        'https://openalex.org/W2',
      ],
    })

    resolveAnswer(
      new Response(JSON.stringify(ragResponse), {
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    expect(
      await screen.findByText(/模型不确定且需要外部知识时应触发检索/),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '[1]' })).toHaveAttribute(
      'href',
      '#citation-1',
    )
    expect(screen.getByRole('link', { name: '[2]' })).toHaveAttribute(
      'href',
      '#citation-2',
    )
    expect(
      screen.getByRole('heading', { level: 4, name: 'Test Paper 3' }),
    ).toBeInTheDocument()
    expect(screen.getByText('W3 · Chunk 4')).toBeInTheDocument()
    expect(screen.getByText('第 3 页')).toBeInTheDocument()
    expect(screen.getByText('Methods')).toBeInTheDocument()
    expect(screen.getByText('0.8736')).toBeInTheDocument()
    expect(
      screen.getByText('Uncertainty indicates that retrieval is needed.'),
    ).toBeInTheDocument()
    expect(screen.getByText('页码未提供')).toBeInTheDocument()
    expect(screen.getByText('章节未提供')).toBeInTheDocument()
  })

  it('shows a request-level RAG error without hiding prior workflow results', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    renderHome()
    const user = await runSearch(fetchMock)
    await prepareSelection(fetchMock, user)
    fetchMock.mockImplementationOnce(() =>
      jsonResponse(successfulIngestionResponse()),
    )
    await user.click(screen.getByRole('button', { name: '确认摄取' }))
    await screen.findByText('已处理 3 篇论文')
    fetchMock.mockImplementationOnce(() =>
      jsonResponse({ detail: '问答服务暂时不可用' }, 502),
    )

    await user.type(
      screen.getByRole('textbox', { name: '研究问题' }),
      'When should retrieval happen?',
    )
    await user.click(screen.getByRole('button', { name: '提问' }))

    expect(
      await screen.findByText('问答请求失败：问答服务暂时不可用'),
    ).toBeInTheDocument()
    expect(screen.getByText('已检查 3 篇论文')).toBeInTheDocument()
    expect(screen.getByText('已处理 3 篇论文')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提问' })).toBeEnabled()
  })
})
