import React from 'react'
import AlphaArenaFeed from './AlphaArenaFeed'
import ArenaAnalyticsFeed from './ArenaAnalyticsFeed'

interface ArenaFeedSwitcherProps {
    showAlpha: boolean
    refreshKey: number | undefined
    wsRef: React.RefObject<WebSocket | null> | undefined
    selectedAccount: number | 'all' | undefined
    onSelectedAccountChange: (account: number | 'all') => void
}

/**
 * Wrapper component that conditionally renders either AlphaArenaFeed or ArenaAnalyticsFeed.
 * 
 * IMPORTANT: We use true conditional rendering (not CSS hiding) to ensure React
 * only tracks hooks for the currently visible component. This prevents hooks
 * order conflicts when child components (like Tabs) use useMemo internally.
 */
function ArenaFeedSwitcherComponent({
    showAlpha,
    refreshKey,
    wsRef,
    selectedAccount,
    onSelectedAccountChange,
}: ArenaFeedSwitcherProps) {
    // Use true conditional rendering with stable wrapper to avoid hooks conflicts
    // React will only track hooks for the rendered component
    // Using a stable wrapper div ensures React properly unmounts/remounts components
    return (
        <div className="absolute inset-0 overflow-auto">
            {showAlpha ? (
                <AlphaArenaFeed
                    key="alpha-arena-feed-stable"
                    refreshKey={refreshKey ?? 0}
                    wsRef={wsRef}
                    selectedAccount={selectedAccount}
                    onSelectedAccountChange={onSelectedAccountChange}
                />
            ) : (
                <ArenaAnalyticsFeed
                    key="arena-analytics-feed-stable"
                    refreshKey={refreshKey ?? 0}
                    selectedAccount={selectedAccount}
                    onSelectedAccountChange={onSelectedAccountChange}
                />
            )}
        </div>
    )
}

// Export without memo to avoid any hooks-related issues
export default ArenaFeedSwitcherComponent
