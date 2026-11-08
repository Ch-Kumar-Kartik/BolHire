// Small original line icons. Decorative: always aria-hidden, the surrounding control carries the text.

const base = { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true } as const

export function ChatIcon() {
  return (
    <svg {...base}>
      <path d="M5 5.5h14a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5h-7l-4.5 3.5v-3.5H5A1.5 1.5 0 0 1 3.5 15V7A1.5 1.5 0 0 1 5 5.5Z" />
      <path d="M8 10h8M8 13h5" />
    </svg>
  )
}

export function InboxIcon({ size = 18 }: { size?: number }) {
  return (
    <svg {...base} width={size} height={size}>
      <path d="M4 13.5 6.2 6.3A1.5 1.5 0 0 1 7.6 5.2h8.8a1.5 1.5 0 0 1 1.4 1.1L20 13.5" />
      <path d="M4 13.5h4.5l1.2 2.2h4.6l1.2-2.2H20v4.3a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.8Z" />
    </svg>
  )
}

export function PersonIcon() {
  return (
    <svg {...base}>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 19.5c1.2-3.3 3.8-5 7-5s5.8 1.7 7 5" />
    </svg>
  )
}

export function ChevronIcon() {
  return (
    <svg {...base} width={16} height={16}>
      <path d="m9 6 6 6-6 6" />
    </svg>
  )
}
