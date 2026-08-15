import type {
  IngestSourceType,
  IngestStatus,
  PaperIngestResponse,
} from '../../entities/paper/model'
import styles from './IngestionResults.module.css'

const statusLabels: Record<IngestStatus, string> = {
  ingested: '已摄取',
  cached: '已使用缓存',
  abstract_fallback: '已回退原始摘要',
  license_review_required: '需要许可证复核',
  unavailable: '语料不可用',
  failed: '处理失败',
}

const sourceLabels: Record<IngestSourceType, string> = {
  grobid_xml: 'GROBID XML',
  pdf: 'PDF',
  abstract: '原始摘要',
}

interface IngestionResultsProps {
  data: PaperIngestResponse
}

export function IngestionResults({ data }: IngestionResultsProps) {
  return (
    <section className={styles.section} aria-labelledby="ingestion-title">
      <div className={styles.heading}>
        <div>
          <p>语料摄取结果</p>
          <h2 id="ingestion-title">已处理 {data.count} 篇论文</h2>
        </div>
        <span>逐篇结果</span>
      </div>
      <ol className={styles.list}>
        {data.papers.map((paper) => (
          <li key={paper.paper_id} className={styles.item}>
            <div className={styles.title}>
              <h3>{paper.title ?? '未提供标题'}</h3>
              <p>{paper.paper_id}</p>
            </div>
            <strong className={styles.status} data-status={paper.status}>
              {statusLabels[paper.status]}
            </strong>
            <dl className={styles.details}>
              <div>
                <dt>来源</dt>
                <dd>
                  {paper.source_type
                    ? sourceLabels[paper.source_type]
                    : '无可用来源'}
                </dd>
              </div>
              {paper.license && (
                <div>
                  <dt>许可证</dt>
                  <dd>{paper.license}</dd>
                </div>
              )}
              <div>
                <dt>Segments</dt>
                <dd>{paper.segment_count}</dd>
              </div>
              <div>
                <dt>字符数</dt>
                <dd>{paper.character_count}</dd>
              </div>
              <div>
                <dt>来自缓存</dt>
                <dd>{paper.from_cache ? '是' : '否'}</dd>
              </div>
            </dl>
            {paper.message && <p className={styles.message}>{paper.message}</p>}
          </li>
        ))}
      </ol>
    </section>
  )
}
