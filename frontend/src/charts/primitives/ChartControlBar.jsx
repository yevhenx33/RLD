import React from "react";
import ControlCell from "../../components/common/ControlCell";
import SettingsButton from "../../components/shared/SettingsButton";
import MobileDropdown from "../../components/common/MobileDropdown";
import { DEPLOYMENT_DATE } from "../../utils/helpers";


/**
 * Shared chart control bar (timeframe + resolution + custom date range).
 * Used by both App (Terminal) and Markets pages.
 *
 * @param {Object} props
 * @param {Object} props.controls - Output from useChartControls hook
 * @param {Array} props.timeframes - Quick range options, e.g. [{ l: "1D", d: 1 }, ...]
 * @param {Array} props.resolutions - Resolution options, e.g. ["1H", "4H", "1D", "1W"]
 * @param {boolean} props.showCustomRange - Whether to show the custom date range inputs (default: true)
 * @param {React.ReactNode} props.extraControls - Additional controls to render (e.g. TWAR in App)
 * @param {number} props.columns - Grid columns for the bar (default: auto based on content)
 */
export default function ChartControlBar({
  controls,
  timeframes = [
    { l: "1D", d: 1 },
    { l: "1W", d: 7 },
    { l: "1M", d: 30 },
    { l: "3M", d: 90 },
    { l: "1Y", d: 365 },
    { l: "ALL", d: 9999 },
  ],
  resolutions = ["1H", "4H", "1D", "1W"],
  showCustomRange = true,
  extraControls = null,
}) {
  const {
    tempStart,
    tempEnd,
    activeRange,
    resolution,
    setTempStart,
    setTempEnd,
    setResolution,
    handleApplyDate,
    handleQuickRange,
  } = controls;

  // Determine grid columns based on content
  const colCount = 2 + (showCustomRange ? 1 : 0) + (extraControls ? 1 : 0);
  const gridClass =
    colCount === 2
      ? "grid-cols-2 md:grid-cols-2"
      : colCount === 3
        ? "grid-cols-2 md:grid-cols-3"
        : "grid-cols-2 xl:grid-cols-4";

  return (
    <div
      className={`order-last md:order-none border-y border-white/10 grid ${gridClass}`}
    >
      {/* TIMEFRAME */}
      <ControlCell
        label="TIMEFRAME"
        className="pl-0 border-r md:border-r-0 border-white/10 pr-4 md:pr-4"
      >
        {/* Mobile Dropdown */}
        <MobileDropdown
          value={activeRange}
          options={timeframes.map((o) => ({
            label: o.l,
            value: { d: o.d, l: o.l },
          }))}
          onChange={(v) => handleQuickRange(v.d, v.l)}
        />

        {/* Desktop Buttons */}
        <div className="hidden md:flex w-full gap-0">
          {timeframes.map((btn) => (
            <SettingsButton
              key={btn.l}
              onClick={() => handleQuickRange(btn.d, btn.l)}
              isActive={activeRange === btn.l}
              className="flex-1"
            >
              {btn.l}
            </SettingsButton>
          ))}
        </div>
      </ControlCell>

      {/* RESOLUTION */}
      <ControlCell label="RESOLUTION" className="pl-4 md:pl-4">
        {/* Mobile Dropdown */}
        <MobileDropdown
          value={resolution}
          options={resolutions.map((r) => ({ label: r, value: r }))}
          onChange={(v) => setResolution(v)}
        />

        {/* Desktop Buttons */}
        <div className="hidden md:flex w-full gap-0">
          {resolutions.map((res) => (
            <SettingsButton
              key={res}
              onClick={() => setResolution(res)}
              isActive={resolution === res}
              className="flex-1"
            >
              {res}
            </SettingsButton>
          ))}
        </div>
      </ControlCell>

      {/* CUSTOM RANGE (optional) */}
      {showCustomRange && (
        <ControlCell label="CUSTOM_RANGE" className="pr-0 hidden md:flex">
          <div className="flex items-center justify-between h-[30px] w-full gap-2">
            <input
              type="date"
              value={tempStart}
              min={DEPLOYMENT_DATE}
              onChange={(e) => setTempStart(e.target.value)}
              className="bg-transparent border-b border-white/20 text-sm text-white focus:outline-none focus:border-white font-mono flex-1 py-1 rounded-none text-center"
            />
            <span className="text-gray-600 text-sm">-</span>
            <input
              type="date"
              value={tempEnd}
              min={DEPLOYMENT_DATE}
              onChange={(e) => setTempEnd(e.target.value)}
              className="bg-transparent border-b border-white/20 text-sm text-white focus:outline-none focus:border-white font-mono flex-1 py-1 rounded-none text-center"
            />
            <SettingsButton
              onClick={handleApplyDate}
              className="px-3 h-full flex items-center"
            >
              SET
            </SettingsButton>
          </div>
        </ControlCell>
      )}

      {/* EXTRA CONTROLS (e.g. TWAR in App) */}
      {extraControls}
    </div>
  );
}
