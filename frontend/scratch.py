import re

with open("src/components/charts/Markets.jsx", "r") as f:
    content = f.read()

# Replace FilterDropdown trigger
content = re.sub(
    r'<div className="relative w-full" ref=\{dropdownRef\}>\s*<button\s*onClick=\{([^}]+)\}\s*className={`\s*w-full h-\[30px\] border border-white/20 bg-black flex items-center justify-between px-3 \s*text-sm font-mono text-white focus:outline-none uppercase tracking-widest \s*hover:border-white transition-colors\s*\$\{isOpen \? "border-white" : ""\}\s*`}\s*>\s*<div className="flex items-center gap-2 overflow-hidden">\s*\{label && <span className="text-gray-500">\{label\}:</span>\}\s*<span className="text-cyan-400 font-normal">',
    r'<div className="relative" ref={dropdownRef}>\n      <button\n        onClick={\1}\n        className={`\n          h-[30px] border border-white/20 bg-transparent flex items-center justify-between px-2 gap-2\n          text-xs font-mono text-white focus:outline-none uppercase tracking-widest \n          hover:border-white transition-colors whitespace-nowrap\n          ${isOpen ? "border-white" : ""}\n        `}\n      >\n        <div className="flex items-center gap-1 overflow-hidden">\n          {label && <span className="text-gray-500">{label}:</span>}\n          <span className="text-white font-normal">',
    content
)

# Replace FilterDropdown cyan colors in the dropdown list
content = content.replace('"bg-cyan-500/10 text-cyan-400"', '"bg-white/10 text-white"')
content = content.replace('"bg-cyan-500 border-cyan-500"', '"bg-white border-white"')

# Replace SingleDropdown trigger
content = re.sub(
    r'<div className="relative w-full" ref=\{dropdownRef\}>\s*<button\s*onClick=\{([^}]+)\}\s*className={`w-full h-\[30px\] border border-white/20 bg-black flex items-center justify-between px-3 text-sm font-mono text-white focus:outline-none uppercase tracking-widest hover:border-white transition-colors \$\{isOpen \? "border-white" : ""\}`}\s*>\s*<div className="flex items-center gap-2 overflow-hidden">\s*\{label && <span className="text-gray-500">\{label\}:</span>\}\s*<span className="text-cyan-400 font-normal">',
    r'<div className="relative" ref={dropdownRef}>\n      <button\n        onClick={\1}\n        className={`h-[30px] border border-white/20 bg-transparent flex items-center justify-between px-2 gap-2 text-xs font-mono text-white focus:outline-none uppercase tracking-widest hover:border-white transition-colors whitespace-nowrap ${isOpen ? "border-white" : ""}`}\n      >\n        <div className="flex items-center gap-1 overflow-hidden">\n          {label && <span className="text-gray-500">{label}:</span>}\n          <span className="text-white font-normal">',
    content
)

# Replace SingleDropdown cyan options
content = content.replace('className={`w-full text-left px-3 py-2 text-sm uppercase tracking-widest transition-colors ${selectedValue === opt.value ? "text-cyan-400 bg-white/5" : "text-gray-400 hover:bg-white/5"}`}', 'className={`w-full text-left px-3 py-2 text-xs uppercase tracking-widest transition-colors ${selectedValue === opt.value ? "text-white bg-white/10" : "text-gray-400 hover:bg-white/5"}`}')

# Replace Market layout block
layout_target = """            {/* MERGED CONTROLS & FILTERS */}
            <div className="mt-4 mb-6 pt-3 pb-3 border-y border-white/10 grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
               <div>
                 <FilterDropdown label="Protocols" options={["AAVE", "MORPHO", "EULER", "FLUID"]} selected={selectedProtocols} onChange={setSelectedProtocols} />
               </div>
               <div>
                 <FilterDropdown label="Assets" options={["USDC", "DAI", "USDT"]} selected={selectedAssets} onChange={setSelectedAssets} />
               </div>
               <div>
                 <SingleDropdown label="Timeframe" options={marketTimeframes.map(t=>({label:t.l, value:t.l}))} selectedValue={controls.activeRange} onChange={(val) => { const tf = marketTimeframes.find(t=>t.l===val); controls.handleQuickRange(tf.d, tf.l); }} />
               </div>
               <div>
                 <SingleDropdown label="Resolution" options={["1H","4H","1D","1W"].map(r=>({label:r, value:r}))} selectedValue={resolution} onChange={controls.setResolution} />
               </div>
               
               {/* Custom Range */}
               <div className="flex items-center justify-between h-[30px] w-full gap-2 hidden md:flex col-span-2 lg:col-span-1 xl:col-span-1">
                  <input type="date" value={controls.tempStart} min={DEPLOYMENT_DATE} onChange={(e) => controls.setTempStart(e.target.value)} className="bg-transparent border border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-full min-w-0 h-full rounded-none px-2" />
                  <span className="text-gray-600 text-sm">-</span>
                  <input type="date" value={controls.tempEnd} min={DEPLOYMENT_DATE} onChange={(e) => controls.setTempEnd(e.target.value)} className="bg-transparent border border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-full min-w-0 h-full rounded-none px-2" />
                  <SettingsButton onClick={controls.handleApplyDate} className="px-3 h-full flex items-center flex-shrink-0">SET</SettingsButton>
               </div>
            </div>"""

layout_replacement = """            {/* MERGED CONTROLS & FILTERS */}
            <div className="mt-4 mb-6 flex flex-wrap gap-2 lg:gap-4 items-center">
               <FilterDropdown label="Protocols" options={["AAVE", "MORPHO", "EULER", "FLUID"]} selected={selectedProtocols} onChange={setSelectedProtocols} />
               <FilterDropdown label="Assets" options={["USDC", "DAI", "USDT"]} selected={selectedAssets} onChange={setSelectedAssets} />
               <SingleDropdown label="Timeframe" options={marketTimeframes.map(t=>({label:t.l, value:t.l}))} selectedValue={controls.activeRange} onChange={(val) => { const tf = marketTimeframes.find(t=>t.l===val); controls.handleQuickRange(tf.d, tf.l); }} />
               <SingleDropdown label="Resolution" options={["1H","4H","1D","1W"].map(r=>({label:r, value:r}))} selectedValue={resolution} onChange={controls.setResolution} />
               
               {/* Custom Range */}
               <div className="flex items-center justify-between h-[30px] flex-1 min-w-[250px] gap-2 hidden md:flex">
                  <input type="date" value={controls.tempStart} min={DEPLOYMENT_DATE} onChange={(e) => controls.setTempStart(e.target.value)} className="bg-transparent border border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-full min-w-0 h-full rounded-none px-2" />
                  <span className="text-gray-600 text-sm">-</span>
                  <input type="date" value={controls.tempEnd} min={DEPLOYMENT_DATE} onChange={(e) => controls.setTempEnd(e.target.value)} className="bg-transparent border border-white/20 text-xs text-white focus:outline-none focus:border-white font-mono w-full min-w-0 h-full rounded-none px-2" />
                  <SettingsButton onClick={controls.handleApplyDate} className="px-3 h-[30px] flex items-center flex-shrink-0">SET</SettingsButton>
               </div>
            </div>"""

content = content.replace(layout_target, layout_replacement)

with open("src/components/charts/Markets.jsx", "w") as f:
    f.write(content)

