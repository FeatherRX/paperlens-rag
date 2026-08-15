import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import type { Paper } from '../entities/paper/model'
import { PaperResults } from '../features/paper-search/PaperResults'
import { SearchForm } from '../features/paper-search/SearchForm'
import { IngestionResults } from '../features/paper-selection/IngestionResults'
import { PreparationResults } from '../features/paper-selection/PreparationResults'
import { SelectionToolbar } from '../features/paper-selection/SelectionToolbar'
import { RagQuestionPanel } from '../features/rag-qa/RagQuestionPanel'
import { ingestPapers, preparePapers, searchPapers } from '../shared/api/papers'
import { Feedback } from '../shared/ui/Feedback'
import styles from './HomePage.module.css'

export function HomePage() {
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const search = useMutation({ mutationFn: searchPapers })
  const preparation = useMutation({ mutationFn: preparePapers })
  const ingestion = useMutation({ mutationFn: ingestPapers })

  function handleSearch(query: string) {
    setSelectedIds([])
    preparation.reset()
    ingestion.reset()
    search.mutate(query)
  }

  function handleSelectionChange(paper: Paper, selected: boolean) {
    if (!paper.id) return
    const paperId = paper.id
    setSelectedIds((current) => {
      if (selected) {
        if (current.includes(paperId) || current.length >= 5) return current
        return [...current, paperId]
      }
      return current.filter((id) => id !== paperId)
    })
    preparation.reset()
    ingestion.reset()
  }

  function handlePrepare() {
    ingestion.reset()
    preparation.mutate(selectedIds)
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <a className={styles.brand} href="/" aria-label="PaperLens RAG 首页">
          <span aria-hidden="true">PL</span>
          PaperLens RAG
        </a>
        <p>Research evidence, kept traceable.</p>
      </header>

      <main>
        <section className={styles.hero} aria-labelledby="page-title">
          <p className={styles.kicker}>论文研究助手</p>
          <h1 id="page-title">从研究主题开始，找到值得深入阅读的论文</h1>
          <p className={styles.intro}>
            检索相关论文，亲自选择 3～5 篇；确认后受控摄取合法可用语料，
            再基于可追溯证据提问。
          </p>
          <SearchForm isLoading={search.isPending} onSearch={handleSearch} />
        </section>

        <section className={styles.workspace} aria-label="论文工作区">
          {search.isPending && <Feedback>正在检索相关论文…</Feedback>}
          {search.isError && (
            <Feedback tone="error">
              论文搜索失败：{search.error.message}
            </Feedback>
          )}
          {search.isSuccess && search.data.papers.length === 0 && (
            <Feedback>没有找到相关论文，请尝试调整研究主题。</Feedback>
          )}
          {search.isSuccess && search.data.papers.length > 0 && (
            <>
              <div className={styles.resultsHeading}>
                <div>
                  <p className={styles.kicker}>搜索结果</p>
                  <h2>
                    为“{search.data.query}”找到 {search.data.count} 篇论文
                  </h2>
                </div>
                <span>请选择 3～5 篇</span>
              </div>
              <SelectionToolbar
                selectedCount={selectedIds.length}
                isPreparing={preparation.isPending}
                onPrepare={handlePrepare}
              />
              {preparation.isError && (
                <Feedback tone="error">
                  来源准备失败：{preparation.error.message}
                </Feedback>
              )}
              <PaperResults
                papers={search.data.papers}
                selectedIds={selectedIds}
                onSelectionChange={handleSelectionChange}
              />
            </>
          )}

          {preparation.isSuccess && (
            <PreparationResults
              data={preparation.data}
              isIngesting={ingestion.isPending}
              onIngest={() => ingestion.mutate({ paper_ids: [...selectedIds] })}
            />
          )}
          {ingestion.isError && (
            <Feedback tone="error">
              语料摄取请求失败：{ingestion.error.message}
            </Feedback>
          )}
          {ingestion.isSuccess && (
            <>
              <IngestionResults data={ingestion.data} />
              <RagQuestionPanel paperIds={[...ingestion.variables.paper_ids]} />
            </>
          )}
        </section>
      </main>

      <footer className={styles.footer}>
        <p>Abstract 均为论文原始摘要，不是系统生成的全文总结。</p>
      </footer>
    </div>
  )
}
