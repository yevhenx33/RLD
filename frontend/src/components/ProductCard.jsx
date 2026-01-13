import React from "react";

const ProductCard = ({
  theme = "pink",
  title,
  desc,
  badge,
  Icon,
  onClick,
  isActive,
}) => {
  const themes = {
    pink: {
      text: "text-pink-500",
      bg: "bg-pink-500/10",
      border: "border-pink-500/20",
    },
    cyan: {
      text: "text-cyan-400",
      bg: "bg-cyan-400/10",
      border: "border-cyan-400/20",
    },
  };
  const c = themes[theme];
  return (
    <div
      onClick={onClick}
      className={`border border-white/10 p-4 md:p-6 hover:bg-white/5 transition-colors cursor-pointer group min-h-[120px] md:min-h-[180px] h-full flex flex-col justify-between ${
        isActive ? "bg-white/5" : "bg-[#080808]"
      }`}
    >
      <div>
        <div className="flex justify-between items-center mb-6">
          <span
            className={`text-[10px] font-bold uppercase tracking-widest ${c.text} ${c.bg} px-2 py-1`}
          >
            {badge}
          </span>
          <div className={`${c.border}`}>
            <Icon size={20} className={c.text} />
          </div>
        </div>
        <h3 className="text-lg font-mono text-white mb-2 tracking-tight">
          {title}
        </h3>
        <p className="text-xs text-gray-500 font-mono mb-4 leading-relaxed">
          {desc}
        </p>
      </div>
    </div>
  );
};

export default ProductCard;
