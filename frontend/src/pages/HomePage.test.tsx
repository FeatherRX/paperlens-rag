import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type {
  Paper,
  PaperPrepareResponse,
  PaperSearchResponse,
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
})
