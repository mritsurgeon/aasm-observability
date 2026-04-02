interface Props {
  label: string;
  value: string | number;
  sub?: string;
  accent?: "blue" | "green" | "yellow" | "red";
  onClick?: () => void;
  active?: boolean;
}

export function StatCard({ label, value, sub, accent = "blue", onClick, active }: Props) {
  const valueColors = {
    blue:   "text-blue-400",
    green:  "text-green-400",
    yellow: "text-yellow-400",
    red:    "text-red-400",
  };
  const activeBorderColors = {
    blue:   "border-blue-500",
    green:  "border-green-500",
    yellow: "border-yellow-500",
    red:    "border-red-500",
  };

  return (
    <div
      onClick={onClick}
      className={`bg-gray-800 rounded-lg p-4 transition-colors border ${
        active ? activeBorderColors[accent] : "border-gray-700"
      } ${onClick ? "cursor-pointer hover:bg-gray-700/70 select-none" : ""}`}
    >
      <div className={`text-2xl font-bold font-mono ${valueColors[accent]}`}>{value}</div>
      <div className="text-sm text-gray-400 mt-1">{label}</div>
      {sub && <div className="text-xs text-gray-600 mt-0.5">{sub}</div>}
    </div>
  );
}
