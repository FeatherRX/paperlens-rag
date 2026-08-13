import styles from './SelectionToolbar.module.css'

interface SelectionToolbarProps {
  selectedCount: number
  isPreparing: boolean
  onPrepare: () => void
}

export function SelectionToolbar({
  selectedCount,
  isPreparing,
  onPrepare,
}: SelectionToolbarProps) {
  const canPrepare = selectedCount >= 3 && selectedCount <= 5
  return (
    <aside className={styles.toolbar} aria-label="论文选择状态">
      <div>
        <strong>已选择 {selectedCount}/5</strong>
        <p>
          {selectedCount < 3
            ? `还需选择 ${3 - selectedCount} 篇才能准备分析`
            : '将按你的选择顺序准备论文来源'}
        </p>
      </div>
      <button
        type="button"
        onClick={onPrepare}
        disabled={!canPrepare || isPreparing}
      >
        {isPreparing ? '准备中…' : '准备分析'}
      </button>
    </aside>
  )
}
