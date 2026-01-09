import React from "react";

export default function SettingsButton({
  isActive = false,
  onClick,
  children,
  className = "",
  title,
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`py-1.5 text-xs font-mono focus:outline-none rounded-none transition-colors ${
        isActive
          ? "bg-white text-black hover:bg-white/90"
          : "bg-white/5 hover:bg-white/10 text-gray-400"
      } ${className}`}
    >
      {children}
    </button>
  );
}
