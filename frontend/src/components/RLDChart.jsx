import React, { useState, useEffect, useRef } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";

const CustomTooltip = ({ active, payload, label, resolution }) => {
  if (active && payload && payload.length) {
    const isDaily = resolution === "1D" || resolution === "1W";
    const dateOptions = {
        month: "short",
        day: "numeric",
        year: "numeric"
    };
    
    // Only add time if NOT daily/weekly
    if (!isDaily) {
        dateOptions.hour = "2-digit";
        dateOptions.minute = "2-digit";
    }

    const dateStr = new Date(label * 1000).toLocaleString("en-US", dateOptions);

    return (
      <div className="bg-zinc-950 border border-zinc-800 p-3 rounded shadow-2xl font-mono text-xs z-50">
        <p className="text-zinc-500 mb-2 border-b border-zinc-800 pb-1">
          {dateStr}
        </p>
        {payload.map((entry, index) => (
          <div key={index} className="flex items-center gap-2 mb-1">
            <div
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-zinc-300 font-medium">{entry.name}:</span>
            <span className="text-white font-bold">
              {entry.name && (entry.name.includes("Price") || entry.name.includes("ETH"))
                ? `$${Number(entry.value).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}` 
                : `${Number(entry.value).toFixed(2)}%`}
            </span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

const RLDPerformanceChart = ({ data, areas = [], referenceLines = [], resolution = "1H" }) => {
  // State & Refs
  const containerRef = useRef(null);
  const [yDomain, setYDomain] = useState(['auto', 'auto']);
  const [zoomState, setZoomState] = useState(null); // { start: 0, end: length - 1 }

  // Drag Panning & Animation Frame Refs
  const isDragging = useRef(false);
  const lastMouseX = useRef(0);
  const rafId = useRef(null);

  const prevDataMeta = useRef({ startTs: 0, length: 0 });

  // Initialize/Reset zoom when data changes
  useEffect(() => {
    if (!data || data.length === 0) return;

    const firstTs = data[0].timestamp;
    const currentMeta = prevDataMeta.current;
    
    // Heuristic for "Same Context Update" (Polling):
    // 1. Start Timestamp is roughly the same (tolerance for bucket alignment)
    // 2. Length change is small (incremental update vs timeframe switch)
    const isSameStart = Math.abs(firstTs - currentMeta.startTs) < 3600; 
    const lenDiff = Math.abs(data.length - currentMeta.length);
    const isSmallChange = lenDiff < (currentMeta.length * 0.25) + 5; // 25% or small num
    
    const shouldPreserve = isSameStart && isSmallChange;

    if (shouldPreserve) {
        setZoomState(prev => {
            if (!prev) return { start: 0, end: data.length - 1 };
            // Clamp to new bounds
            // We consciously don't "stick to right edge" here to prevent jumpiness while panning
            return {
                start: Math.min(prev.start, data.length - 1),
                end: Math.min(prev.end, data.length - 1)
            };
        });
    } else {
        // Context switch (New Asset/Resolution) -> Reset
        setZoomState({ start: 0, end: data.length - 1 });
    }

    // Update Meta
    prevDataMeta.current = { startTs: firstTs, length: data.length };
  }, [data]);

  // Derived visible data with Level of Detail (LOD) Downsampling
  // Prevents rendering thousands of SVG nodes which freezes the browser
  const visibleData = React.useMemo(() => {
    if (!data || !zoomState) return [];
    
    // 1. Slice the exact range
    const rawSlice = data.slice(zoomState.start, zoomState.end + 1);
    
    // 2. Downsample if too large (Max 1000 points)
    const MAX_POINTS = 1000;
    if (rawSlice.length <= MAX_POINTS) {
        return rawSlice;
    }
    
    // Simple Nth sampling
    const step = Math.ceil(rawSlice.length / MAX_POINTS);
    const sampled = [];
    for (let i = 0; i < rawSlice.length; i += step) {
        sampled.push(rawSlice[i]);
    }
    // Always include the last point to prevent gaps at current time
    if (sampled[sampled.length - 1] !== rawSlice[rawSlice.length - 1]) {
        sampled.push(rawSlice[rawSlice.length - 1]);
    }
    return sampled;
  }, [data, zoomState]);

  // Auto-Scaling Logic (Triggered when visibleData changes)
  useEffect(() => {
    if (!visibleData || visibleData.length === 0) return;

    let min = Infinity;
    let max = -Infinity;
    
    const leftKeys = areas.filter(a => !a.yAxisId || a.yAxisId === "left").map(a => a.key);

    visibleData.forEach(d => {
        leftKeys.forEach(key => {
            const val = d[key];
            if (val !== undefined && val !== null) {
                if (val < min) min = val;
                if (val > max) max = val;
            }
        });
    });

    if (min === Infinity || max === -Infinity) {
        setYDomain(['auto', 'auto']);
    } else {
        const padding = (max - min) * 0.05;
        const newMin = min >= 0 ? Math.max(0, min - padding) : min - padding;
        setYDomain([newMin, max + padding]);
    }
  }, [visibleData, areas]);

  if (!data || data.length === 0) return null;

  const startTs = data[0].timestamp;
  const endTs = data[data.length - 1].timestamp;
  const totalDuration = endTs - startTs;

  // Zoom & Pan Handler

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !data || data.length === 0) return;

    // --- WHEEL HANDLER (Zoom & Touchpad Pan) ---
    const handleWheel = (e) => {
      e.preventDefault();
      e.stopPropagation();

      setZoomState((currentZoom) => {
        if (!currentZoom) return null;

        const currentLength = currentZoom.end - currentZoom.start;
        const totalLength = data.length;
        
        // 1. HORIZONTAL SCROLL (PANNING)
        // Check for significant deltaX
        if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
            const PAN_SENSITIVITY = 1.0; // Adjust for feel
            // Convert pixels to indices: (deltaX / width) * currentLength
            // We approximate width if resize observer is overkill, or measure ref.
            const width = container.clientWidth || 1000;
            const shiftPixels = e.deltaX * PAN_SENSITIVITY;
            const shiftIndices = (shiftPixels / width) * currentLength * 2.5; // Mult by 2.5 for faster feel
            
            let newStart = currentZoom.start + shiftIndices;
            let newEnd = currentZoom.end + shiftIndices;

            // Clamp Left
            if (newStart < 0) {
                const offset = -newStart;
                newStart += offset;
                newEnd += offset;
            }
            // Clamp Right
            if (newEnd > totalLength - 1) {
                const offset = newEnd - (totalLength - 1);
                newStart -= offset;
                newEnd -= offset;
            }
            
            // Re-clamp boundary check in case window is wider than data (unlikely)
            newStart = Math.max(0, newStart);
            newEnd = Math.min(totalLength - 1, newEnd);

            return {
                start: Math.round(newStart),
                end: Math.round(newEnd)
            };
        }

        // 2. VERTICAL SCROLL (ZOOMING)
        const ZOOM_SPEED = 0.004; 
        const delta = e.deltaY * ZOOM_SPEED;

        // Reject zoom out if full
        if (delta > 0 && currentLength >= totalLength) return currentZoom;
        // Reject zoom in if too small (minimum 5 points)
        if (delta < 0 && currentLength < 5) return currentZoom;

        const zoomFactor = 1 + delta; 
        let newLength = currentLength * zoomFactor;
        newLength = Math.max(5, Math.min(totalLength, newLength));
        
        const lengthDiff = currentLength - newLength;
        let newStart = currentZoom.start + (lengthDiff / 2);
        let newEnd = currentZoom.end - (lengthDiff / 2);

        if (newStart < 0) {
            newEnd -= newStart; 
            newStart = 0;
        }
        if (newEnd > totalLength - 1) {
            newStart -= (newEnd - (totalLength - 1));
            newEnd = totalLength - 1;
        }
        
        return {
            start: Math.round(Math.max(0, newStart)),
            end: Math.round(Math.min(totalLength - 1, newEnd))
        };
      });
    };

    // --- MOUSE DRAG HANDLERS ---
    const handleMouseDown = (e) => {
        isDragging.current = true;
        lastMouseX.current = e.clientX;
        container.style.cursor = 'grabbing';
    };

    const handleMouseMove = (e) => {
        if (!isDragging.current) return;
        
        e.preventDefault(); // Prevent text selection

        if (rafId.current) cancelAnimationFrame(rafId.current);

        rafId.current = requestAnimationFrame(() => {
            const currentClientX = e.clientX;
            const deltaX = lastMouseX.current - currentClientX; 
            lastMouseX.current = currentClientX;

            setZoomState(currentZoom => {
                if (!currentZoom) return null;
                const currentLength = currentZoom.end - currentZoom.start;
                const totalLength = data.length;
                const width = container.clientWidth || 1000;

                const shiftIndices = (deltaX / width) * currentLength;

                let newStart = currentZoom.start + shiftIndices;
                let newEnd = currentZoom.end + shiftIndices;

                if (newStart < 0) {
                   newStart = 0;
                   newEnd = currentZoom.end - currentZoom.start; 
                }
                if (newEnd > totalLength - 1) {
                    newEnd = totalLength - 1;
                    newStart = newEnd - (currentZoom.end - currentZoom.start);
                }
                
                newStart = Math.max(0, newStart);
                newEnd = Math.min(totalLength - 1, newEnd);
                
                return {
                    start: Math.round(newStart),
                    end: Math.round(newEnd)
                };
            });
        });
    };

    const handleMouseUp = () => {
        isDragging.current = false;
        container.style.cursor = 'auto';
        if (rafId.current) cancelAnimationFrame(rafId.current);
    };

    const handleMouseLeave = () => {
        isDragging.current = false;
        container.style.cursor = 'auto';
    };

    // Attach Listeners
    container.addEventListener('wheel', handleWheel, { passive: false });
    container.addEventListener('mousedown', handleMouseDown);
    window.addEventListener('mousemove', handleMouseMove); // Window for smooth dragging outside div
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      container.removeEventListener('wheel', handleWheel);
      container.removeEventListener('mousedown', handleMouseDown);
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [data]); // Only re-bind if data changes

  const formatTick = (unix) => {
    // Dynamic formatting based on visible duration
    if (!visibleData.length) return "";
    const vStart = visibleData[0].timestamp;
    const vEnd = visibleData[visibleData.length - 1].timestamp;
    const vDuration = vEnd - vStart;

    const date = new Date(unix * 1000);
    if (vDuration < 172800) { // < 2 Days
      return date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
    }
    if (vDuration < 15552000) { // < 6 Months
      return date.toLocaleDateString([], { month: "short", day: "numeric" });
    }
    return date.toLocaleDateString([], { month: "short", year: "2-digit" });
  };

  return (
    <div 
        ref={containerRef}
        className="w-full h-full select-none outline-none focus:outline-none"
        style={{ touchAction: 'none' }} // Hint to browser to let us handle touch
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={visibleData} // Use sliced data
          margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
        >
          <defs>
            {areas.map((area, index) => (
              <linearGradient
                key={index}
                id={`gradient-${area.key}`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="5%" stopColor={area.color} stopOpacity={0.33} />
                <stop offset="95%" stopColor={area.color} stopOpacity={0} />
              </linearGradient>
            ))}
          </defs>

          <CartesianGrid
            strokeDasharray="3 3"
            stroke="#27272a"
            vertical={false}
          />

          <XAxis
            dataKey="timestamp"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={formatTick}
            stroke="#71717a"
            fontSize={12}
            tickMargin={12}
            minTickGap={60}
          />

          <YAxis
            stroke="#71717a"
            fontSize={12}
            domain={yDomain}
            tickFormatter={(val) => `${Number(val).toFixed(1)}%`}
            width={50}
            allowDataOverflow={true}
          />
          
          {areas.some((a) => a.yAxisId === "right") && (
            <YAxis
              yAxisId="right"
              orientation="right"
              stroke="#71717a"
              fontSize={12}
              domain={["auto", "auto"]}
              tickFormatter={(val) => `$${val}`}
              width={60}
            />
          )}

          <Tooltip
            content={<CustomTooltip resolution={resolution} />}
            cursor={{ stroke: "#52525b", strokeDasharray: "4 4" }}
          />

          {areas.map((area, index) => (
            <Area
              key={index}
              {...(area.yAxisId ? { yAxisId: area.yAxisId } : {})}
              type="monotone"
              dataKey={area.key}
              stroke={area.color}
              strokeWidth={2}
              fill={`url(#gradient-${area.key})`}
              name={area.name}
              isAnimationActive={false}
              connectNulls={true}
            />
          ))}

          {referenceLines.map((line, index) => (
            <ReferenceLine
              key={index}
              y={line.y}
              stroke={line.stroke || "#ef4444"}
              strokeDasharray="3 3"
              label={{
                position: "right",
                value: line.label,
                fill: line.stroke,
                fontSize: 10,
              }}
            />
          ))}
          
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};

export default RLDPerformanceChart;
