"use client";

export function ClientTime({ date }: { date: string | Date }) {
  return (
    <span suppressHydrationWarning>
      {new Date(date).toLocaleTimeString()}
    </span>
  );
}
