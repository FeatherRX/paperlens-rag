import type { Paper } from '../../entities/paper/model'
import styles from './PaperCard.module.css'

interface PaperCardProps {
  paper: Paper
  selected: boolean
  selectionDisabled: boolean
  onSelectionChange: (paper: Paper, selected: boolean) => void
}

function listText(values: string[], fallback: string) {
  return values.length > 0 ? values.join('、') : fallback
}

function openAccessText(paper: Paper) {
  if (paper.open_access?.is_oa === true) {
    return paper.open_access.status
      ? `开放获取 · ${paper.open_access.status}`
      : '开放获取'
  }
  if (paper.open_access?.is_oa === false) return '非开放获取'
  return '开放状态未知'
}

export function PaperCard({
  paper,
  selected,
  selectionDisabled,
  onSelectionChange,
}: PaperCardProps) {
  const title = paper.title ?? '未提供标题'
  const link = paper.doi ?? paper.landing_page_url

  return (
    <article className={`${styles.card} ${selected ? styles.selected : ''}`}>
      <div className={styles.headingRow}>
        <div>
          <p className={styles.eyebrow}>
            {paper.publication_year ?? '年份未知'} · {openAccessText(paper)}
          </p>
          <h3 className={styles.title}>{title}</h3>
        </div>
        <label className={styles.checkboxLabel}>
          <input
            type="checkbox"
            checked={selected}
            disabled={selectionDisabled}
            onChange={(event) => onSelectionChange(paper, event.target.checked)}
            aria-label={`选择论文：${title}`}
          />
          <span>{selected ? '已选择' : '选择'}</span>
        </label>
      </div>

      <dl className={styles.metadata}>
        <div>
          <dt>作者</dt>
          <dd>{listText(paper.authors, '未提供')}</dd>
        </div>
        <div>
          <dt>机构</dt>
          <dd>{listText(paper.institutions, '未提供')}</dd>
        </div>
        <div>
          <dt>引用数</dt>
          <dd>{paper.cited_by_count ?? '未知'}</dd>
        </div>
      </dl>

      <div className={styles.abstractBlock}>
        <h4>论文原始摘要</h4>
        <p>{paper.abstract ?? 'OpenAlex 暂未提供该论文摘要。'}</p>
      </div>

      {link ? (
        <a className={styles.link} href={link} target="_blank" rel="noreferrer">
          查看论文来源
        </a>
      ) : (
        <span className={styles.noLink}>暂无 DOI 或落地页</span>
      )}
    </article>
  )
}
