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
 * This ensures React always sees the same component type at the same position,
 * preventing hooks confusion.
 */
export default function ArenaFeedSwitcher({
    showAlpha,
    refreshKey,
    wsRef,
    selectedAccount,
    onSelectedAccountChange,
}: ArenaFeedSwitcherProps) {
    // Always render both components, but use CSS to hide/show
    // This ensures hooks are always called in the same order
    return (
        <>
            <div
                className="absolute inset-0 overflow-auto"
                style={{
                    visibility: showAlpha ? 'visible' : 'hidden',
                    opacity: showAlpha ? 1 : 0,
                    pointerEvents: showAlpha ? 'auto' : 'none',
                    zIndex: showAlpha ? 1 : 0,
                }}
            >
                <AlphaArenaFeed
                    refreshKey={refreshKey}
                    wsRef={wsRef}
                    selectedAccount={selectedAccount}
                    onSelectedAccountChange={onSelectedAccountChange}
                />
            </div>
            <div
                className="absolute inset-0 overflow-auto"
                style={{
                    visibility: showAlpha ? 'hidden' : 'visible',
                    opacity: showAlpha ? 0 : 1,
                    pointerEvents: showAlpha ? 'none' : 'auto',
                    zIndex: showAlpha ? 0 : 1,
                }}
            >
                <ArenaAnalyticsFeed
                    refreshKey={refreshKey}
                    selectedAccount={selectedAccount}
                    onSelectedAccountChange={onSelectedAccountChange}
                />
            </div>
        </>
    )
}

