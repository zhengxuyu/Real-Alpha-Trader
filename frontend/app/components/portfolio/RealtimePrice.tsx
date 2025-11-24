import React, { useEffect, useRef, useState } from 'react'
import FlipNumber from './FlipNumber'

interface RealtimePriceProps {
  symbol: string
  wsRef?: React.MutableRefObject<WebSocket | null>
  className?: string
}

export default function RealtimePrice({ symbol, wsRef, className = "" }: RealtimePriceProps) {
  const [price, setPrice] = useState<number | null>(null)
  const [priceChange, setPriceChange] = useState<'up' | 'down' | null>(null)

  // Shared latest timestamp in milliseconds across WebSocket + HTTP
  const lastTimestampRef = useRef<number>(0)
  const abortControllerRef = useRef<AbortController | null>(null)

  // Helper to apply a new price with animation, assuming timestamp has been checked
  const applyPriceUpdate = (newPrice: number) => {
    setPrice(prevPrice => {
      if (prevPrice !== null && prevPrice !== newPrice) {
        setPriceChange(newPrice > prevPrice ? 'up' : 'down')
        // Clear the change indicator after animation
        setTimeout(() => setPriceChange(null), 1000)
      }
      return newPrice
    })
  }

  useEffect(() => {
    // Reset when symbol changes
    lastTimestampRef.current = 0
    setPrice(null)
  }, [symbol])


  useEffect(() => {
    // WebSocket listener for real-time price updates
    if (!wsRef?.current) return

    const handleMessage = (event: MessageEvent) => {
      try {
        const message = JSON.parse(event.data)
        if (message?.type === 'price_update' && message.symbol === symbol) {
          const newPrice = Number(message.price)
          if (Number.isNaN(newPrice)) return

          // Backend sends `timestamp` as seconds; normalize to ms
          let tsMs: number | null = null
          if (typeof message.timestamp === 'number') {
            tsMs = message.timestamp * 1000
          } else if (typeof message.timestamp_ms === 'number') {
            tsMs = message.timestamp_ms
          }
          if (tsMs == null) {
            tsMs = Date.now()
          }

          if (tsMs <= lastTimestampRef.current) return
          lastTimestampRef.current = tsMs

          applyPriceUpdate(newPrice)
        }
      } catch {
        // Ignore non-JSON messages
      }
    }

    const ws = wsRef.current
    ws.addEventListener('message', handleMessage)

    return () => {
      ws.removeEventListener('message', handleMessage)
    }
  }, [wsRef, symbol])

  useEffect(() => {
    // HTTP fallback for initial price fetch and as backup if WebSocket lags
    let isMounted = true

    const fetchPrice = async () => {
      if (!isMounted) return

      // Cancel previous HTTP request if still in-flight
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
      const controller = new AbortController()
      abortControllerRef.current = controller

      try {
        const response = await fetch(`/api/market/price/${symbol}`, {
          signal: controller.signal,
        })
        if (!response.ok) return

        const data = await response.json()
        const newPrice = Number(data.price)
        if (Number.isNaN(newPrice)) return

        const tsMs: number =
          typeof data.timestamp === 'number' ? data.timestamp : Date.now()

        // Drop stale or duplicate responses
        if (tsMs <= lastTimestampRef.current) return

        lastTimestampRef.current = tsMs
        applyPriceUpdate(newPrice)
      } catch (error: any) {
        if (error?.name === 'AbortError') {
          return
        }
        console.error(`Error fetching price for ${symbol}:`, error)
      }
    }

    // Initial fetch
    fetchPrice()

    // Reduced frequency since WebSocket should handle real-time updates
    const interval = setInterval(fetchPrice, 5000) // 5 seconds fallback

    return () => {
      isMounted = false
      clearInterval(interval)
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [symbol])

  if (price === null) {
    return (
      <div className={`text-xs text-muted-foreground ${className}`}>
        --
      </div>
    )
  }

  return (
    <div className={`flex items-center gap-1 ${className}`}>
      <FlipNumber
        value={price}
        prefix="$"
        decimals={2}
        className={`text-xs font-medium transition-colors duration-300 ${
          priceChange === 'up'
            ? 'text-green-500'
            : priceChange === 'down'
            ? 'text-red-500'
            : 'text-muted-foreground'
        }`}
      />
      {priceChange && (
        <span className={`text-xs transition-opacity duration-1000 ${
          priceChange === 'up' ? 'text-green-500' : 'text-red-500'
        }`}>
          {priceChange === 'up' ? '↗' : '↘'}
        </span>
      )}
    </div>
  )
}