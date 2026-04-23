import React from "react";
import { MetricCell, StatItem } from "../../components/pools/MetricsGrid";
import { Activity, PieChart, Layers, Users } from "lucide-react";

export default function LendingDataPage() {
  return (
    <div className="min-h-screen bg-[#050505] text-gray-300 font-mono">
      <main className="max-w-[1800px] mx-auto px-6 pb-12">
        <section className="pt-8 pb-6 border-b border-white/10 mb-6 w-full justify-start">
          <h1 className="text-2xl text-white font-semibold tracking-tight uppercase">
            DATA
          </h1>
          <p className="text-sm text-gray-500 uppercase tracking-widest mt-2">
            Lending market monitor
          </p>
        </section>

        <div className="mb-6 w-full">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 border border-white/10 bg-[#080808] divide-y md:divide-y-0 md:divide-x divide-white/10">
            <MetricCell
              label="OVERVIEW"
              Icon={PieChart}
              hideLabelOnMobile={false}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="TOTAL NET WORTH" value="$15.2B" change="+2.4%" />
                  </div>
                  <div className="flex flex-col justify-center gap-2 border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="TOTAL SUPPLY" value="$22.8B" change="+1.8%" />
                    <StatItem label="TOTAL BORROW" value="$7.6B" change="-0.5%" />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="RATES"
              Icon={Activity}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="AVG SUPPLY" value="4.54%" change="+0.12%" />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="AVG BORROW" value="6.12%" change="-0.04%" />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="TVL_BY_TYPE"
              Icon={Layers}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="POOLED" value="$22.3B" change="+1.5%" />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="ISOLATED" value="$500M" change="+12.4%" />
                  </div>
                </div>
              }
            />
            <MetricCell
              label="STATS"
              Icon={Users}
              hideLabelOnMobile={true}
              content={
                <div className="flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto">
                  <div className="flex flex-col justify-end">
                    <StatItem label="MARKETS" value="142" change="+2" />
                  </div>
                  <div className="flex flex-col justify-end border-t md:border-t-0 md:border-l border-white/10 pt-3 md:pt-0 md:pl-4">
                    <StatItem label="USERS" value="124,500" change="+1,200" />
                  </div>
                </div>
              }
            />
          </div>
        </div>

      </main>
    </div>
  );
}
