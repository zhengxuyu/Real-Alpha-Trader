import React, { useEffect, useRef, useState } from 'react'
import FlipNumber from '../portfolio/FlipNumber'

interface PriceTickerProps {
  symbol: string
  name: string
}

export default function PriceTicker({ symbol, name }: PriceTickerProps) {
  const [price, setPrice] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  // Track the latest timestamp (ms) we've applied to avoid stale updates
  const lastTimestampRef = useRef<number>(0)
  const abortControllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    let isMounted = true

    const fetchPrice = async () => {
      if (!isMounted) return

      // Cancel any in-flight request before starting a new one
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
      const controller = new AbortController()
      abortControllerRef.current = controller

      try {
        const response = await fetch(`/api/market-data/price/${symbol}`, {
          signal: controller.signal,
        })
        if (!response.ok) return

        const data = await response.json()
        const newPrice = Number(data.price)
        const tsMs: number =
          typeof data.timestamp === 'number' ? data.timestamp : Date.now()

        if (Number.isNaN(newPrice)) return

        // Drop stale responses (older or equal timestamp)
        if (tsMs <= lastTimestampRef.current) return

        lastTimestampRef.current = tsMs
        setPrice(newPrice)
      } catch (error: any) {
        if (error?.name === 'AbortError') {
          // Request was cancelled, this is expected when a newer request starts
          return
        }
        console.error(`Error fetching price for ${symbol}:`, error)
      } finally {
        if (isMounted) {
          setLoading(false)
        }
      }
    }

    fetchPrice()
    const interval = setInterval(fetchPrice, 1500) // 1.5 seconds

    return () => {
      isMounted = false
      clearInterval(interval)
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [symbol])

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-md bg-muted/70 px-3 py-2 shadow-sm border border-border/70 w-[140px]">
        <div className="flex flex-col leading-tight">
          <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {symbol.split('/')[0]}
          </span>
          <div className="text-sm font-semibold text-muted-foreground">--</div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 rounded-md bg-muted/70 px-3 py-2 shadow-sm border border-border/70 w-[140px]">
      <div className="flex flex-col leading-tight">
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
          {symbol.split('/')[0]}
        </span>
        {price !== null ? (
          <FlipNumber
            value={price}
            prefix="$"
            decimals={2}
            className="text-sm font-semibold text-primary"
          />
        ) : (
          <div className="text-sm font-semibold text-muted-foreground">--</div>
        )}
      </div>
    </div>
  )
}
