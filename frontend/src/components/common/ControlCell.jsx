import React from "react";

const ControlCell = React.memo(function ControlCell({ label, children, className = "" }) {
  return (
    <div className={`p-4 flex flex-col gap-3 ${className}`}>
      <span className="text-sm text-gray-500 uppercase tracking-[0.2em] font-bold">
        {label}
      </span>
      <div className="flex items-center w-full flex-wrap gap-y-2">
        {children}
      </div>
    </div>
  );
});

export default ControlCell;
