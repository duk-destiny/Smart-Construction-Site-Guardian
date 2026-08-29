import { motion } from 'framer-motion'
import type { ReactNode } from 'react'

interface DockItem {
  key: string
  icon: ReactNode
  label: string
  active?: boolean
  onClick: () => void
}

export default function Dock({ items }: { items: DockItem[] }) {
  return (
    <motion.nav
      initial={{ y: 80, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ delay: 0.3, type: 'spring', stiffness: 260, damping: 20 }}
      style={{
        position: 'fixed',
        bottom: 20,
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 1000,
        display: 'flex',
        gap: 4,
        padding: '8px 12px',
        borderRadius: 20,
        background: 'rgba(10, 14, 26, 0.85)',
        backdropFilter: 'blur(24px)',
        WebkitBackdropFilter: 'blur(24px)',
        border: '1px solid rgba(255,255,255,0.08)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.03)',
      }}
    >
      {items.map((item) => (
        <DockButton key={item.key} item={item} />
      ))}
    </motion.nav>
  )
}

function DockButton({ item }: { item: DockItem }) {
  return (
    <motion.button
      onClick={item.onClick}
      whileHover={{ scale: 1.15, y: -4 }}
      whileTap={{ scale: 0.95 }}
      transition={{ type: 'spring', stiffness: 400, damping: 17 }}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 4,
        padding: '10px 16px',
        border: 'none',
        borderRadius: 14,
        background: item.active ? 'rgba(200, 16, 46, 0.15)' : 'transparent',
        cursor: 'pointer',
        color: item.active ? '#c8102e' : 'rgba(255,255,255,0.5)',
        transition: 'background 0.2s, color 0.2s',
      }}
    >
      <span style={{ fontSize: 20, lineHeight: 1 }}>{item.icon}</span>
      <span style={{
        fontSize: 10,
        fontWeight: 500,
        letterSpacing: '0.02em',
        whiteSpace: 'nowrap',
        opacity: item.active ? 1 : 0.7,
      }}>{item.label}</span>
      {item.active && (
        <motion.div
          layoutId="dock-indicator"
          style={{
            position: 'absolute',
            bottom: 4,
            width: 4,
            height: 4,
            borderRadius: 2,
            background: '#c8102e',
            boxShadow: '0 0 8px rgba(200,16,46,0.6)',
          }}
          transition={{ type: 'spring', stiffness: 350, damping: 30 }}
        />
      )}
    </motion.button>
  )
}
