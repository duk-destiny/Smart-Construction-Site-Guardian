import { motion } from 'framer-motion'

export default function PageHeader({ title, subtitle, action }: {
  title: string
  subtitle?: string
  action?: React.ReactNode
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      style={{
        display: 'flex',
        alignItems: 'flex-end',
        justifyContent: 'space-between',
        marginBottom: 24,
        gap: 16,
      }}
    >
      <div>
        <h2 style={{
          fontSize: 22,
          fontWeight: 700,
          color: 'var(--text-strong)',
          margin: 0,
          letterSpacing: '-0.01em',
        }}>{title}</h2>
        {subtitle && (
          <p style={{
            fontSize: 13,
            color: 'rgba(var(--fg-rgb),0.35)',
            margin: '6px 0 0',
          }}>{subtitle}</p>
        )}
      </div>
      {action && <div>{action}</div>}
    </motion.div>
  )
}
