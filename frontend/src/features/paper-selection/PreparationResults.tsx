import type {
  PaperPrepareResponse,
  SourceStatus,
} from '../../entities/paper/model'
import styles from './PreparationResults.module.css'

const statusCopy: Record<SourceStatus, { label: string; description: string }> =
  {
    fulltext_candidate: {
      label: '开放全文候选',
      description: '尚未下载，且尚未完成许可证复核。',
    },
    abstract_only: {
      label: '仅原始摘要',
      description: '当前仅可使用论文原始摘要。',
    },
    unavailable: {
      label: '语料不可用',
      description: '暂时没有可分析语料。',
    },
  }

interface PreparationResultsProps {
  data: PaperPrepareResponse
  isIngesting: boolean
  onIngest: () => void
}

export function PreparationResults({
  data,
  isIngesting,
  onIngest,
}: PreparationResultsProps) {
  return (
    <section className={styles.section} aria-labelledby="preparation-title">
      <div className={styles.heading}>
        <div>
          <p className={styles.kicker}>来源准备结果</p>
          <h2 id="preparation-title">已检查 {data.count} 篇论文</h2>
        </div>
        <span>未下载全文</span>
      </div>
      <ol className={styles.list}>
        {data.papers.map((paper) => {
          const copy = statusCopy[paper.source_status]
          return (
            <li key={paper.id ?? paper.title} className={styles.item}>
              <div>
                <h3>{paper.title ?? '未提供标题'}</h3>
                <p>{paper.id}</p>
              </div>
              <div className={styles.status} data-status={paper.source_status}>
                <strong>{copy.label}</strong>
                <span>{copy.description}</span>
              </div>
            </li>
          )
        })}
      </ol>
      <div className={styles.action}>
        <div>
          <strong>确认摄取已选择论文</strong>
          <p>将重新核验许可证，并受控获取可用正文或保存原始摘要。</p>
        </div>
        <button type="button" onClick={onIngest} disabled={isIngesting}>
          {isIngesting ? '摄取中…' : '确认摄取'}
        </button>
      </div>
    </section>
  )
}
