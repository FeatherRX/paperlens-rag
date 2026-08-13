import type { Paper } from '../../entities/paper/model'
import { PaperCard } from './PaperCard'
import styles from './PaperResults.module.css'

interface PaperResultsProps {
  papers: Paper[]
  selectedIds: string[]
  onSelectionChange: (paper: Paper, selected: boolean) => void
}

export function PaperResults({
  papers,
  selectedIds,
  onSelectionChange,
}: PaperResultsProps) {
  return (
    <div className={styles.list} aria-label="论文搜索结果">
      {papers.map((paper, index) => {
        const id = paper.id ?? `paper-${index}`
        const selected = paper.id ? selectedIds.includes(paper.id) : false
        return (
          <PaperCard
            key={id}
            paper={paper}
            selected={selected}
            selectionDisabled={
              !paper.id || (!selected && selectedIds.length >= 5)
            }
            onSelectionChange={onSelectionChange}
          />
        )
      })}
    </div>
  )
}
