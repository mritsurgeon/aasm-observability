interface Props {
  label: string;
  value: string | number;
  sub?: string;
  accent?: "blue" | "green" | "yellow" | "red";
}

export function StatCard({ label, value, sub, accent = "blue" }: Props) {
  const colors = {
    blue:   "text-blue-400",
    green:  "text-green-400",
    yellow: "text-yellow-400",
    red:    "text-red-400",
  };
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
      <div className={`text-2xl font-bold font-mono ${colors[accent]}`}>{value}</div>
      <div className="text-sm text-gray-400 mt-1">{label}</div>
      {sub && <div className="text-xs text-gray-600 mt-0.5">{sub}</div>}
    </div>
  );
}
