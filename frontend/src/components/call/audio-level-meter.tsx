const BAR_COUNT = 12;

type AudioLevelMeterProps = {
  /** Audio level in the range 0..1. */
  level: number;
  active: boolean;
  label: string;
  tone: "caller" | "agent";
};

/** A row of bars that light up with the current audio level. */
export function AudioLevelMeter({ level, active, label, tone }: AudioLevelMeterProps) {
  // Speech levels are usually small; a square root makes quiet speech visible.
  const lit = active ? Math.round(Math.min(1, Math.sqrt(Math.max(0, level))) * BAR_COUNT) : 0;
  const litColor = tone === "caller" ? "bg-sky-500" : "bg-emerald-500";

  return (
    <div className="flex flex-col gap-2" aria-label={`${label} audio level`}>
      <span className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </span>
      <div className="flex h-10 items-end gap-1" role="meter" aria-valuemin={0} aria-valuemax={BAR_COUNT} aria-valuenow={lit}>
        {Array.from({ length: BAR_COUNT }, (_, i) => (
          <span
            key={i}
            className={`w-2 rounded-full transition-all duration-75 ${
              i < lit ? litColor : "bg-zinc-200 dark:bg-zinc-800"
            }`}
            style={{ height: `${30 + (i / (BAR_COUNT - 1)) * 70}%` }}
          />
        ))}
      </div>
    </div>
  );
}
