import type { ReactNode } from 'react'
import styles from './Feedback.module.css'

interface FeedbackProps {
  tone?: 'neutral' | 'error'
  children: ReactNode
}

export function Feedback({ tone = 'neutral', children }: FeedbackProps) {
  return (
    <div
      className={`${styles.feedback} ${tone === 'error' ? styles.error : ''}`}
      role={tone === 'error' ? 'alert' : 'status'}
    >
      {children}
    </div>
  )
}
