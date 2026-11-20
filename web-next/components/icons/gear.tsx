export function GearIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M18.69 9.95L21.98 10.42L21.98 13.58L18.69 14.05L18.18 15.29L20.17 17.94L17.94 20.17L15.29 18.18L14.05 18.69L13.58 21.98L10.42 21.98L9.95 18.69L8.71 18.18L6.06 20.17L3.83 17.94L5.82 15.29L5.31 14.05L2.02 13.58L2.02 10.42L5.31 9.95L5.82 8.71L3.83 6.06L6.06 3.83L8.71 5.82L9.95 5.31L10.42 2.02L13.58 2.02L14.05 5.31L15.29 5.82L17.94 3.83L20.17 6.06L18.18 8.71Z" />
      <circle cx="12" cy="12" r="3.1" />
    </svg>
  );
}
