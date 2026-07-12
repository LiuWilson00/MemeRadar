/** 可多選標籤（設定偏好、搜尋更多引導共用）。 */
export default function Chip({
  label,
  active,
  onToggle,
}: {
  label: string;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className={`rounded-full border px-3 py-1.5 text-sm active:scale-95 ${
        active
          ? "border-amber bg-amber-soft text-amber"
          : "border-line text-muted active:bg-panel"
      }`}
    >
      {label}
    </button>
  );
}

export function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}
