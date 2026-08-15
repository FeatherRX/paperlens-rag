import { useMutation } from '@tanstack/react-query'
import { Fragment, useState, type FormEvent } from 'react'
import type { RagAnswerResponse, RagCitation } from '../../entities/paper/model'
import { answerRag } from '../../shared/api/rag'
import { Feedback } from '../../shared/ui/Feedback'
import styles from './RagQuestionPanel.module.css'

interface RagQuestionPanelProps {
  paperIds: string[]
}

const MINIMUM_RAG_PAPERS = 3

export function RagQuestionPanel({ paperIds }: RagQuestionPanelProps) {
  const [query, setQuery] = useState('')
  const answer = useMutation({ mutationFn: answerRag })
  const normalizedQuery = query.trim()
  const canAsk = paperIds.length >= MINIMUM_RAG_PAPERS

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canAsk || !normalizedQuery || answer.isPending) return
    answer.mutate({
      query: normalizedQuery,
      paper_ids: [...paperIds],
    })
  }

  return (
    <section className={styles.section} aria-labelledby="rag-question-title">
      <div className={styles.heading}>
        <div>
          <p>证据问答</p>
          <h2 id="rag-question-title">基于已摄取论文提问</h2>
        </div>
        <span>{paperIds.length} 篇可用于问答</span>
      </div>

      {!canAsk && (
        <Feedback>
          当前只有 {paperIds.length} 篇论文形成了可检索本地文档；至少需要 3
          篇才能发起问答。请调整选择并重新准备、摄取。
        </Feedback>
      )}

      <form className={styles.form} onSubmit={handleSubmit}>
        <label htmlFor="rag-query">研究问题</label>
        <textarea
          id="rag-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="例如：主动式 RAG 应在什么情况下检索外部信息？"
          rows={3}
          disabled={!canAsk}
        />
        <div className={styles.formFooter}>
          <p>回答只使用当前这 {paperIds.length} 篇可检索论文的证据。</p>
          <button
            type="submit"
            disabled={!canAsk || !normalizedQuery || answer.isPending}
          >
            {answer.isPending ? '生成回答中…' : '提问'}
          </button>
        </div>
      </form>

      {answer.isPending && <Feedback>正在检索证据并生成回答，请稍候…</Feedback>}
      {answer.isError && (
        <Feedback tone="error">问答请求失败：{answer.error.message}</Feedback>
      )}
      {answer.isSuccess && <AnswerResult data={answer.data} />}
    </section>
  )
}

function AnswerResult({ data }: { data: RagAnswerResponse }) {
  return (
    <div className={styles.result}>
      <div className={styles.answer}>
        <p className={styles.resultLabel}>回答</p>
        <AnswerText answer={data.answer} citations={data.citations} />
      </div>
      <div className={styles.citations}>
        <div className={styles.citationHeading}>
          <h3>引用证据</h3>
          <span>{data.citations.length} 条</span>
        </div>
        {data.citations.length === 0 ? (
          <p className={styles.emptyCitations}>本次回答没有返回引用证据。</p>
        ) : (
          <ol className={styles.citationList}>
            {data.citations.map((citation) => (
              <CitationItem
                key={citation.citation_number}
                citation={citation}
              />
            ))}
          </ol>
        )}
      </div>
    </div>
  )
}

function AnswerText({
  answer,
  citations,
}: {
  answer: string
  citations: RagCitation[]
}) {
  const validNumbers = new Set(
    citations.map((citation) => citation.citation_number),
  )
  return (
    <p className={styles.answerText}>
      {answer.split(/(\[\d+\])/g).map((part, index) => {
        const match = /^\[(\d+)\]$/.exec(part)
        const number = match ? Number(match[1]) : null
        return number !== null && validNumbers.has(number) ? (
          <a key={`${part}-${index}`} href={`#citation-${number}`}>
            {part}
          </a>
        ) : (
          <Fragment key={`${part}-${index}`}>{part}</Fragment>
        )
      })}
    </p>
  )
}

function CitationItem({ citation }: { citation: RagCitation }) {
  const pages = citation.page_numbers.length
    ? `第 ${citation.page_numbers.join('、')} 页`
    : '页码未提供'
  return (
    <li id={`citation-${citation.citation_number}`} className={styles.citation}>
      <div className={styles.citationTitle}>
        <span>[{citation.citation_number}]</span>
        <div>
          <h4>{citation.paper_title ?? '未提供标题'}</h4>
          <p>
            {citation.paper_id} · Chunk {citation.chunk_index}
          </p>
        </div>
      </div>
      <dl className={styles.metadata}>
        <div>
          <dt>页码</dt>
          <dd>{pages}</dd>
        </div>
        <div>
          <dt>章节</dt>
          <dd>{citation.section_title ?? '章节未提供'}</dd>
        </div>
        <div>
          <dt>相关度</dt>
          <dd>{citation.retrieval_score.toFixed(4)}</dd>
        </div>
      </dl>
      <blockquote>{citation.evidence_excerpt}</blockquote>
    </li>
  )
}
