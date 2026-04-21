import React from 'react'

interface LayoutProps {
  children: React.ReactNode
}

/**
 * Flex row som fyller tilgjengeleg høgd (utan Header). overflow-hidden let
 * barna (sidebar + main) scrolle uavhengig. Plasserast i ein flex-col wrapper
 * saman med Header i App.tsx.
 */
export default function Layout({ children }: LayoutProps) {
  return (
    <div className="flex flex-1 min-h-0 bg-gray-100 overflow-hidden">
      {children}
    </div>
  )
}
