import { useState, type FormEvent } from 'react'
import styles from './SearchForm.module.css'

interface SearchFormProps {
  isLoading: boolean
  onSearch: (query: string) => void
}

export function SearchForm({ isLoading, onSearch }: SearchFormProps) {
  const [query, setQuery] = useState('')

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const normalized = query.trim()
    if (normalized) onSearch(normalized)
  }

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <label className={styles.label} htmlFor="research-topic">
        研究主题
      </label>
      <div className={styles.controls}>
        <input
          id="research-topic"
          className={styles.input}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="例如：retrieval augmented generation"
          autoComplete="off"
        />
        <button
          className={styles.button}
          type="submit"
          disabled={isLoading || !query.trim()}
        >
          {isLoading ? '检索中…' : '搜索论文'}
        </button>
      </div>
      <p className={styles.hint}>默认检索相关性最高的 10 篇论文。</p>
    </form>
  )
}
